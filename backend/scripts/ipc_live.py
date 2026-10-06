"""
模拟运行期间的实时命令消费（共享模块）
------------------------------------
为什么需要单独一个模块：脚本原本**只在所有轮次跑完后**才轮询 ipc_commands/，
所以"运行中注入变量"在结构上不可能生效。要在轮循环里响应注入，就必须在轮内读
一次命令目录。

但轮内**不能**直接调用各脚本的 `process_commands()`：它会处理 interview，
而 interview 内部要 `await` 一次 LLM 调用——那会把模拟主循环卡住。所以这里只
认领 `inject_event`，**其余命令一律原样留在目录里**，交给轮次结束后的
`process_commands()` 正常处理。

纯文件 IO，不依赖 OASIS，因此可以离线单测。
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

INJECT_EVENT = "inject_event"


def _commands_dir(simulation_dir: str) -> str:
    return os.path.join(simulation_dir, "ipc_commands")


def _responses_dir(simulation_dir: str) -> str:
    return os.path.join(simulation_dir, "ipc_responses")


def pending_commands(simulation_dir: str) -> List[Tuple[str, Dict[str, Any]]]:
    """
    列出待处理命令，按修改时间升序（先进先出）。

    Returns:
        [(文件路径, 命令内容), ...]
    """
    directory = _commands_dir(simulation_dir)
    if not os.path.isdir(directory):
        return []

    entries: List[Tuple[str, float]] = []
    for filename in os.listdir(directory):
        if not filename.endswith('.json'):
            continue
        filepath = os.path.join(directory, filename)
        try:
            entries.append((filepath, os.path.getmtime(filepath)))
        except OSError:
            continue
    entries.sort(key=lambda item: item[1])

    commands: List[Tuple[str, Dict[str, Any]]] = []
    for filepath, _ in entries:
        try:
            with open(filepath, 'r', encoding='utf-8') as handle:
                commands.append((filepath, json.load(handle)))
        except (json.JSONDecodeError, OSError):
            # 写到一半的文件跳过即可，下一次轮询会重试
            continue
    return commands


def write_response(
    simulation_dir: str,
    command_id: str,
    status: str,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None
) -> None:
    """写回命令响应，供 Flask 侧轮询取走。"""
    directory = _responses_dir(simulation_dir)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{command_id}.json")
    payload = {
        "command_id": command_id,
        "status": status,
        "result": result,
        "error": error,
        "timestamp": datetime.now().isoformat(),
    }
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def consume_live_injections(simulation_dir: str) -> List[Dict[str, Any]]:
    """
    认领并消费**仅** inject_event 命令。

    其余命令（interview / batch_interview / close_env）不删除、不响应，
    留给轮次结束后的 process_commands()。

    Returns:
        可直接发布的 [{'poster_agent_id': int, 'content': str}, ...]
    """
    events: List[Dict[str, Any]] = []

    for filepath, command in pending_commands(simulation_dir):
        if command.get('command_type') != INJECT_EVENT:
            continue

        command_id = command.get('command_id') or os.path.basename(filepath)[:-5]
        args = command.get('args') or {}
        content = str(args.get('content') or '').strip()
        try:
            agent_id = int(args.get('agent_id', 0))
        except (TypeError, ValueError):
            agent_id = 0

        if not content:
            write_response(simulation_dir, command_id, "failed", error="事件内容为空")
            _remove(filepath)
            continue

        events.append({'poster_agent_id': agent_id, 'content': content})
        write_response(
            simulation_dir, command_id, "completed",
            result={"accepted": True, "agent_id": agent_id, "content": content},
        )
        _remove(filepath)

    return events


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------- 事件触发的共用逻辑 ----------------
#
# 放在这里而不是各脚本里，是为了让三个脚本（parallel / twitter / reddit）共用同一套
# 语义。oasis 的导入放在函数内部，这样本模块在没装 oasis 的环境里也能被导入与单测。

def events_for_round(
    scheduled_events: Optional[List[Dict[str, Any]]],
    round_num: int
) -> List[Dict[str, Any]]:
    """
    取出某个轮次要触发的预排事件。

    纯函数，便于离线测试。`round` 与主循环的 round_num 对齐（从 0 开始）：
    round=0 表示**第一轮 Agent 互动**——初始帖子在那之前就已经发过了。
    """
    if not scheduled_events:
        return []

    due: List[Dict[str, Any]] = []
    for event in scheduled_events:
        if not isinstance(event, dict):
            continue
        try:
            target = int(event.get('round', 0))
        except (TypeError, ValueError):
            continue
        if target == round_num and str(event.get('content') or '').strip():
            due.append(event)
    return due


async def fire_manual_posts(
    env,
    events: List[Dict[str, Any]],
    agent_names: Optional[Dict[int, str]] = None,
    action_logger=None,
    round_label: int = 0,
    source: str = 'scheduled'
) -> int:
    """
    把一批 {poster_agent_id, content} 事件以 ManualAction 形式发布，返回成功条数。

    逐个 env.step 而不是打包成一次：Twitter 与 Reddit 对"同一 Agent 多个动作"的
    接受形式不同（前者单动作、后者列表），逐个发在两个平台上都正确，
    而事件数量本就不多。
    """
    from oasis import ActionType, ManualAction

    names = agent_names or {}
    fired = 0

    for event in events:
        agent_id = event.get('poster_agent_id', 0)
        content = str(event.get('content') or '').strip()
        if not content:
            continue
        try:
            agent = env.agent_graph.get_agent(agent_id)
            await env.step({
                agent: ManualAction(
                    action_type=ActionType.CREATE_POST,
                    action_args={'content': content}
                )
            })
        except Exception as exc:
            print(f"[{source}] 事件发布失败 (agent_id={agent_id}): {exc}")
            continue

        fired += 1
        if action_logger:
            action_logger.log_action(
                round_num=round_label,
                agent_id=agent_id,
                agent_name=names.get(agent_id, f"Agent_{agent_id}"),
                action_type="CREATE_POST",
                action_args={"content": content}
            )

    return fired

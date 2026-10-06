"""
事件注入接线的静态一致性测试

存在的理由：模拟脚本是**子进程**，只在真正跑一次模拟时才会被加载。任何
"模块能导入、但名字没绑定"或"命令类型没对齐"的错误都会在那之前完全隐形——
而发现它的代价是一次真实的模拟（几分钟 + 一笔 LLM 花费）。

所以这里不执行模拟，只做静态检查：三个脚本 + IPC 客户端的命令类型、
处理函数、轮循环消费点是否齐备，以及引用的名字是否真的绑定到了模块上。
"""

import importlib
import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SCRIPTS = os.path.join(BACKEND, 'scripts')

if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

SIMULATION_SCRIPTS = [
    'run_parallel_simulation.py',
    'run_twitter_simulation.py',
    'run_reddit_simulation.py',
]

#: 脚本必须从共享模块导入的名字
REQUIRED_BINDINGS = ('consume_live_injections', 'events_for_round', 'fire_manual_posts')


@pytest.mark.parametrize('filename', SIMULATION_SCRIPTS)
def test_script_declares_inject_event_command(filename):
    source = open(os.path.join(SCRIPTS, filename), encoding='utf-8').read()
    assert 'INJECT_EVENT = "inject_event"' in source, f'{filename} 缺少 INJECT_EVENT 命令类型'
    assert 'async def handle_inject_event' in source, f'{filename} 缺少 handle_inject_event'
    assert 'CommandType.INJECT_EVENT:' in source, f'{filename} 的 process_commands 没有分派 INJECT_EVENT'


@pytest.mark.parametrize('filename', SIMULATION_SCRIPTS)
def test_script_consumes_injections_inside_round_loop(filename):
    """轮循环内必须轮询注入，否则"运行中注入变量"不可能生效。"""
    source = open(os.path.join(SCRIPTS, filename), encoding='utf-8').read()
    assert 'consume_live_injections(' in source, f'{filename} 没有在轮循环里消费注入'
    assert 'events_for_round(' in source, f'{filename} 没有触发预排事件'


@pytest.mark.parametrize('filename', SIMULATION_SCRIPTS)
def test_script_binds_shared_helpers(filename):
    """
    模块能导入 ≠ 名字已绑定。

    这一条正是为了拦住那次真实发生过的疏漏：导入行没插进去时模块照样能 import
    成功，但一跑到轮循环就 NameError——而那已经是花钱跑到一半了。
    """
    module_name = filename[:-3]
    module = importlib.import_module(module_name)
    missing = [name for name in REQUIRED_BINDINGS if not hasattr(module, name)]
    assert not missing, f'{module_name} 未绑定: {missing}（检查 from ipc_live import 是否真的存在）'


def test_client_and_scripts_agree_on_command_value():
    """Flask 侧与脚本侧的命令字符串必须字面一致，否则命令会被判为未知类型。"""
    from app.services.simulation_ipc import CommandType as ClientCommandType

    client_value = ClientCommandType.INJECT_EVENT.value
    assert client_value == 'inject_event'

    for filename in SIMULATION_SCRIPTS:
        source = open(os.path.join(SCRIPTS, filename), encoding='utf-8').read()
        assert f'INJECT_EVENT = "{client_value}"' in source, f'{filename} 的命令值与客户端不一致'


def test_inject_event_arg_contract():
    """
    客户端发出的参数名必须与脚本读取的一致：agent_id / content / platform。
    """
    import inspect

    from app.services.simulation_ipc import SimulationIPCClient

    signature = inspect.signature(SimulationIPCClient.send_inject_event)
    assert {'agent_id', 'content', 'platform'} <= set(signature.parameters)

    for filename in SIMULATION_SCRIPTS:
        source = open(os.path.join(SCRIPTS, filename), encoding='utf-8').read()
        for arg in ('agent_id', 'content'):
            assert f'args.get("{arg}"' in source, f'{filename} 没有读取参数 {arg}'

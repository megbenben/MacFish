"""
图谱相关API路由
采用项目上下文机制，服务端持久化状态
"""

import os
import json
import traceback
import threading
from typing import Any, Dict, Optional
from flask import request, jsonify

from . import graph_bp
from ..config import Config
from ..services.ontology_generator import OntologyGenerator
from ..services.graph_builder import GraphBuilderService
from ..services.local_graph_store import LocalGraphStore
from ..services.text_processor import TextProcessor
from ..utils.file_parser import FileParser, ExtractionError
from ..utils.logger import get_logger
from ..utils.locale import t, get_locale, set_locale
from ..models.task import TaskManager, TaskStatus
from ..models.project import ProjectManager, ProjectStatus

# 获取日志器
logger = get_logger('mirofish.api')


def allowed_file(filename: str) -> bool:
    """检查文件扩展名是否允许"""
    if not filename or '.' not in filename:
        return False
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    return ext in Config.ALLOWED_EXTENSIONS


# ============== 项目管理接口 ==============

@graph_bp.route('/project/<project_id>', methods=['GET'])
def get_project(project_id: str):
    """
    获取项目详情
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": t('api.projectNotFound', id=project_id)
        }), 404

    return jsonify({
        "success": True,
        "data": project.to_dict()
    })


@graph_bp.route('/project/list', methods=['GET'])
def list_projects():
    """
    列出所有项目
    """
    limit = request.args.get('limit', 50, type=int)
    projects = ProjectManager.list_projects(limit=limit)
    
    return jsonify({
        "success": True,
        "data": [p.to_dict() for p in projects],
        "count": len(projects)
    })


@graph_bp.route('/project/<project_id>', methods=['DELETE'])
def delete_project(project_id: str):
    """
    删除项目
    """
    success = ProjectManager.delete_project(project_id)
    
    if not success:
        return jsonify({
            "success": False,
            "error": t('api.projectDeleteFailed', id=project_id)
        }), 404

    return jsonify({
        "success": True,
        "message": t('api.projectDeleted', id=project_id)
    })


@graph_bp.route('/project/<project_id>/reset', methods=['POST'])
def reset_project(project_id: str):
    """
    重置项目状态（用于重新构建图谱）
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": t('api.projectNotFound', id=project_id)
        }), 404

    # 重置到本体已生成状态
    if project.ontology:
        project.status = ProjectStatus.ONTOLOGY_GENERATED
    else:
        project.status = ProjectStatus.CREATED
    
    project.graph_id = None
    project.graph_build_task_id = None
    project.error = None
    ProjectManager.save_project(project)
    
    return jsonify({
        "success": True,
        "message": t('api.projectReset', id=project_id),
        "data": project.to_dict()
    })


# ============== 接口1：上传文件并生成本体 ==============

@graph_bp.route('/ontology/generate', methods=['POST'])
def generate_ontology():
    """
    接口1：上传文件，分析生成本体定义
    
    请求方式：multipart/form-data
    
    参数：
        files: 上传的文件（PDF/MD/TXT），可多个
        simulation_requirement: 模拟需求描述（必填）
        project_name: 项目名称（可选）
        additional_context: 额外说明（可选）
        
    返回：
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "ontology": {
                    "entity_types": [...],
                    "edge_types": [...],
                    "analysis_summary": "..."
                },
                "files": [...],
                "total_text_length": 12345
            }
        }
    """
    try:
        logger.info("=== 开始生成本体定义 ===")
        
        # 获取参数
        simulation_requirement = request.form.get('simulation_requirement', '')
        project_name = request.form.get('project_name', 'Unnamed Project')
        additional_context = request.form.get('additional_context', '')
        
        logger.debug(f"项目名称: {project_name}")
        logger.debug(f"模拟需求: {simulation_requirement[:100]}...")
        
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationRequirement')
            }), 400
        
        # 获取上传的文件
        uploaded_files = request.files.getlist('files')
        if not uploaded_files or all(not f.filename for f in uploaded_files):
            return jsonify({
                "success": False,
                "error": t('api.requireFileUpload')
            }), 400
        
        # 创建项目
        project = ProjectManager.create_project(name=project_name)
        project.simulation_requirement = simulation_requirement
        logger.info(f"创建项目: {project.project_id}")
        
        # 保存文件并提取文本
        #
        # 逐个文件 try/except：一个坏文件不应该让整批上传丢掉（以前会冒泡成 500），
        # 而且不支持/解析失败的文件必须把原因带回给用户 —— 以前是被静默跳过的。
        document_texts = []
        all_text = ""
        file_results = []

        for file in uploaded_files:
            if not file or not file.filename:
                continue

            filename = file.filename

            if not FileParser.is_supported(filename):
                # 包含旧版 Office（.doc/.ppt/.xls）—— 给出「另存为」这类可操作的提示
                file_results.append({
                    "filename": filename,
                    "ok": False,
                    "error": FileParser.explain_unsupported(filename),
                })
                continue

            try:
                # 保存文件到项目目录
                file_info = ProjectManager.save_file_to_project(
                    project.project_id,
                    file,
                    filename
                )

                # 提取文本
                result = FileParser.extract_text_detailed(file_info["path"])
                text = TextProcessor.preprocess_text(result.text)
                if not text.strip():
                    raise ExtractionError(t('api.emptyDocument'))

                document_texts.append(text)
                all_text += f"\n\n=== {file_info['original_filename']} ===\n{text}"
                project.files.append({
                    "filename": file_info["original_filename"],
                    "size": file_info["size"]
                })
                file_results.append({
                    "filename": filename,
                    "ok": True,
                    "chars": len(text),
                    "notes": result.notes,
                })
            except Exception as exc:
                logger.warning(f"文件解析失败 {filename}: {exc}")
                file_results.append({
                    "filename": filename,
                    "ok": False,
                    "error": str(exc),
                })

        if not document_texts:
            ProjectManager.delete_project(project.project_id)
            details = [f"{r['filename']}: {r.get('error', '')}"
                       for r in file_results if not r.get('ok')]
            return jsonify({
                "success": False,
                "error": t('api.noDocProcessed') + ("\n" + "\n".join(details) if details else ""),
                "data": {"files": file_results}
            }), 400
        
        # 保存提取的文本
        project.total_text_length = len(all_text)
        ProjectManager.save_extracted_text(project.project_id, all_text)
        logger.info(f"文本提取完成，共 {len(all_text)} 字符")
        
        # 生成本体
        logger.info("调用 LLM 生成本体定义...")
        generator = OntologyGenerator()
        ontology = generator.generate(
            document_texts=document_texts,
            simulation_requirement=simulation_requirement,
            additional_context=additional_context if additional_context else None
        )
        
        # 保存本体到项目
        entity_count = len(ontology.get("entity_types", []))
        edge_count = len(ontology.get("edge_types", []))
        logger.info(f"本体生成完成: {entity_count} 个实体类型, {edge_count} 个关系类型")
        
        project.ontology = {
            "entity_types": ontology.get("entity_types", []),
            "edge_types": ontology.get("edge_types", [])
        }
        project.analysis_summary = ontology.get("analysis_summary", "")
        project.status = ProjectStatus.ONTOLOGY_GENERATED
        ProjectManager.save_project(project)
        logger.info(f"=== 本体生成完成 === 项目ID: {project.project_id}")
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project.project_id,
                "project_name": project.name,
                "ontology": project.ontology,
                "analysis_summary": project.analysis_summary,
                "files": project.files,
                "file_results": file_results,
                "total_text_length": project.total_text_length
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _count_graph_references(graph_id: str) -> int:
    """统计有多少个模拟仍然指向这张图谱。

    删掉图谱会让这些模拟的「图谱记忆更新」失去落点（报告里存的 graph_id 也会悬空），
    所以重建前要把这个数字告诉用户，而不是默默删掉。
    """
    if not graph_id:
        return 0
    sim_root = Config.OASIS_SIMULATION_DATA_DIR
    if not os.path.isdir(sim_root):
        return 0

    count = 0
    for entry in os.listdir(sim_root):
        state_file = os.path.join(sim_root, entry, 'state.json')
        if not os.path.exists(state_file):
            continue
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                if json.load(f).get('graph_id') == graph_id:
                    count += 1
        except Exception:
            continue
    return count


# ============== 接口2：构建图谱 ==============

@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """
    接口2：根据project_id构建图谱
    
    请求（JSON）：
        {
            "project_id": "proj_xxxx",  // 必填，来自接口1
            "graph_name": "图谱名称",    // 可选
            "chunk_size": 500,          // 可选，默认500
            "chunk_overlap": 50         // 可选，默认50
        }
        
    返回：
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "task_id": "task_xxxx",
                "message": "图谱构建任务已启动"
            }
        }
    """
    try:
        logger.info("=== 开始构建图谱 ===")
        
        # 检查配置
        from ..settings import runtime_settings
        cfg = runtime_settings.get_llm_config()
        errors = []
        if not cfg['api_key'] and runtime_settings._data.get('provider') == 'network':
            errors.append(t('api.llmApiKeyMissing'))
        if errors:
            logger.error(f"配置错误: {errors}")
            return jsonify({
                "success": False,
                "error": t('api.configError', details="; ".join(errors))
            }), 500
        
        # 解析请求
        data = request.get_json() or {}
        project_id = data.get('project_id')
        logger.debug(f"请求参数: project_id={project_id}")
        
        if not project_id:
            return jsonify({
                "success": False,
                "error": t('api.requireProjectId')
            }), 400
        
        # 获取项目
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": t('api.projectNotFound', id=project_id)
            }), 404

        # 检查项目状态
        force = data.get('force', False)  # 强制重新构建
        
        if project.status == ProjectStatus.CREATED:
            return jsonify({
                "success": False,
                "error": t('api.ontologyNotGenerated')
            }), 400
        
        if project.status == ProjectStatus.GRAPH_BUILDING and not force:
            return jsonify({
                "success": False,
                "error": t('api.graphBuilding'),
                "task_id": project.graph_build_task_id
            }), 400

        # 图谱已经建过一次、而且确实有节点时，「重建」意味着要回答一个产品问题：
        # 旧图谱是继续累积，还是丢掉重建？
        # 以前 force 只是把 project.graph_id 置空 —— 旧图谱的 nodes/edges/episodes
        # 永远留在 graphs.db 里，无人引用也无人清理（每重建一次多一份）。
        # 现在不传 force 就先返回 409 + 现有规模，让前端问一声，再由用户决定。
        existing_graph_id = project.graph_id
        existing_node_count = 0
        if existing_graph_id:
            try:
                existing_node_count = LocalGraphStore.get_instance().get_node_count(existing_graph_id)
            except Exception as e:
                logger.warning(f"读取现有图谱节点数失败（按 0 处理）: {existing_graph_id}, error={e}")

        if existing_graph_id and existing_node_count > 0 and not force:
            ref_count = _count_graph_references(existing_graph_id)
            return jsonify({
                "success": False,
                "error": t('api.graphRebuildConfirm', count=existing_node_count, refs=ref_count),
                "data": {
                    "need_confirm": True,
                    "graph_id": existing_graph_id,
                    "node_count": existing_node_count,
                    "referenced_simulations": ref_count,
                },
            }), 409

        # 强制重建：重置状态，并**真正删掉**旧图谱，避免孤儿数据一直堆在 graphs.db 里
        if force and project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.FAILED, ProjectStatus.GRAPH_COMPLETED]:
            if existing_graph_id:
                try:
                    GraphBuilderService(api_key=Config.ZEP_API_KEY).delete_graph(existing_graph_id)
                    logger.info(f"强制重建：已删除旧图谱 {existing_graph_id}（{existing_node_count} 个节点）")
                except Exception as e:
                    logger.warning(f"删除旧图谱失败，继续重建（旧数据可能残留）: {existing_graph_id}, error={e}")
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            project.graph_id = None
            project.graph_build_task_id = None
            project.error = None

        # 获取配置
        graph_name = data.get('graph_name', project.name or 'MiroFish Graph')
        chunk_size = data.get('chunk_size', project.chunk_size or Config.DEFAULT_CHUNK_SIZE)
        chunk_overlap = data.get('chunk_overlap', project.chunk_overlap or Config.DEFAULT_CHUNK_OVERLAP)

        # 校验切块参数：切块逻辑的下一步是 `start = end - overlap`，
        # 一旦 overlap >= chunk_size 就会原地打转，而后台线程不会抛错也不会超时，
        # 只会让任务永远停在 building 并持续吃内存。这里挡在最外层。
        try:
            chunk_size = int(chunk_size)
            chunk_overlap = int(chunk_overlap)
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": t('api.invalidChunkConfig', size=chunk_size, overlap=chunk_overlap)
            }), 400

        # overlap 不只要小于 chunk_size，还得留出足够的推进量：overlap 越接近
        # chunk_size，块数就越接近「文本长度」（每轮只前进几个字符），一份 1MB 的
        # 材料能切出上百万个块。默认是 50/500（10%），这里放宽到 50% 已经是上限。
        if not (1 <= chunk_size <= Config.MAX_CHUNK_SIZE) or not (0 <= chunk_overlap <= chunk_size // 2):
            return jsonify({
                "success": False,
                "error": t('api.invalidChunkConfig', size=chunk_size, overlap=chunk_overlap)
            }), 400

        # 更新项目配置
        project.chunk_size = chunk_size
        project.chunk_overlap = chunk_overlap
        
        # 获取提取的文本
        text = ProjectManager.get_extracted_text(project_id)
        if not text:
            return jsonify({
                "success": False,
                "error": t('api.textNotFound')
            }), 400
        
        # 获取本体
        ontology = project.ontology
        if not ontology:
            return jsonify({
                "success": False,
                "error": t('api.ontologyNotFound')
            }), 400
        
        # 创建异步任务
        task_manager = TaskManager()
        task_id = task_manager.create_task(f"构建图谱: {graph_name}")
        logger.info(f"创建图谱构建任务: task_id={task_id}, project_id={project_id}")
        
        # 更新项目状态
        project.status = ProjectStatus.GRAPH_BUILDING
        project.graph_build_task_id = task_id
        ProjectManager.save_project(project)
        
        # Capture locale before spawning background thread
        current_locale = get_locale()

        # 启动后台任务
        def build_task():
            set_locale(current_locale)
            build_logger = get_logger('mirofish.build')
            try:
                build_logger.info(f"[{task_id}] 开始构建图谱...")
                task_manager.update_task(
                    task_id, 
                    status=TaskStatus.PROCESSING,
                    message=t('progress.initGraphService')
                )
                
                # 创建图谱构建服务
                builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
                
                # 分块
                task_manager.update_task(
                    task_id,
                    message=t('progress.textChunking'),
                    progress=5
                )
                chunks = TextProcessor.split_text(
                    text, 
                    chunk_size=chunk_size, 
                    overlap=chunk_overlap
                )
                total_chunks = len(chunks)
                
                # 创建图谱
                task_manager.update_task(
                    task_id,
                    message=t('progress.creatingZepGraph'),
                    progress=10
                )
                graph_id = builder.create_graph(name=graph_name)
                
                # 更新项目的graph_id
                project.graph_id = graph_id
                ProjectManager.save_project(project)
                
                # 设置本体
                task_manager.update_task(
                    task_id,
                    message=t('progress.settingOntology'),
                    progress=15
                )
                builder.set_ontology(graph_id, ontology)
                
                # 添加文本（progress_callback 签名是 (msg, progress_ratio)）
                def add_progress_callback(msg, progress_ratio):
                    progress = 15 + int(progress_ratio * 40)  # 15% - 55%
                    task_manager.update_task(
                        task_id,
                        message=msg,
                        progress=progress
                    )
                
                task_manager.update_task(
                    task_id,
                    message=t('progress.addingChunks', count=total_chunks),
                    progress=15
                )
                
                episode_uuids = builder.add_text_batches(
                    graph_id, 
                    chunks,
                    batch_size=3,
                    progress_callback=add_progress_callback
                )
                
                # 等待Zep处理完成（查询每个episode的processed状态）
                task_manager.update_task(
                    task_id,
                    message=t('progress.waitingZepProcess'),
                    progress=55
                )
                
                def wait_progress_callback(msg, progress_ratio):
                    progress = 55 + int(progress_ratio * 35)  # 55% - 90%
                    task_manager.update_task(
                        task_id,
                        message=msg,
                        progress=progress
                    )
                
                builder._wait_for_episodes(episode_uuids, wait_progress_callback)
                
                # 获取图谱数据
                task_manager.update_task(
                    task_id,
                    message=t('progress.fetchingGraphData'),
                    progress=95
                )
                graph_data = builder.get_graph_data(graph_id)
                
                # 更新项目状态
                project.status = ProjectStatus.GRAPH_COMPLETED
                ProjectManager.save_project(project)
                
                node_count = graph_data.get("node_count", 0)
                edge_count = graph_data.get("edge_count", 0)
                build_logger.info(f"[{task_id}] 图谱构建完成: graph_id={graph_id}, 节点={node_count}, 边={edge_count}")
                
                # 完成
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    message=t('progress.graphBuildComplete'),
                    progress=100,
                    result={
                        "project_id": project_id,
                        "graph_id": graph_id,
                        "node_count": node_count,
                        "edge_count": edge_count,
                        "chunk_count": total_chunks
                    }
                )
                
            except Exception as e:
                # 更新项目状态为失败
                build_logger.error(f"[{task_id}] 图谱构建失败: {str(e)}")
                build_logger.debug(traceback.format_exc())
                
                project.status = ProjectStatus.FAILED
                project.error = str(e)
                ProjectManager.save_project(project)
                
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    message=t('progress.buildFailed', error=str(e)),
                    error=traceback.format_exc()
                )
        
        # 启动后台线程
        thread = threading.Thread(target=build_task, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "task_id": task_id,
                "message": t('api.graphBuildStarted', taskId=task_id)
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 任务查询接口 ==============

def _infer_graph_task(task_id: str) -> Optional[Dict[str, Any]]:
    """任务表里查不到时，按 project.graph_build_task_id 反推图谱任务的状态。

    任务本身会落盘（uploads/tasks/），但历史任务会被清理、文件也可能被手动删掉。
    图谱构建任务 ID 同时记在项目上，据此推断可以避免前端在「构建期间重启过」之后
    一直转圈——这正是 OPTIMIZATION.md 1.5 里那条「只有图谱任务没有补偿」。
    """
    try:
        projects = ProjectManager.list_projects(limit=200)
    except Exception as e:
        logger.warning(f"推断图谱任务状态失败: {e}")
        return None

    for project in projects:
        if project.graph_build_task_id != task_id:
            continue

        if project.status == ProjectStatus.GRAPH_COMPLETED and project.graph_id:
            status, progress = "completed", 100
            message, error = t('progress.taskComplete'), None
        elif project.status == ProjectStatus.FAILED or project.error:
            status, progress = "failed", 0
            message, error = t('progress.taskFailed'), project.error
        else:
            # 任务记录都不在了，却还写着「构建中」——说明构建它的那个进程已经没了
            status, progress = "failed", 0
            message = t('api.graphBuildLost')
            error = message

        return {
            "task_id": task_id,
            "task_type": f"构建图谱: {project.name or project.project_id}",
            "status": status,
            "progress": progress,
            "message": message,
            "progress_detail": {},
            "result": {
                "project_id": project.project_id,
                "graph_id": project.graph_id,
            } if status == "completed" else None,
            "error": error,
            "metadata": {"project_id": project.project_id},
            # 标记这不是任务表里的原记录，而是推断出来的
            "inferred": True,
        }

    return None


@graph_bp.route('/task/<task_id>', methods=['GET'])
def get_task(task_id: str):
    """
    查询任务状态
    """
    task = TaskManager().get_task(task_id)

    if not task:
        inferred = _infer_graph_task(task_id)
        if inferred:
            return jsonify({
                "success": True,
                "data": inferred
            })
        return jsonify({
            "success": False,
            "error": t('api.taskNotFound', id=task_id)
        }), 404

    return jsonify({
        "success": True,
        "data": task.to_dict()
    })


@graph_bp.route('/tasks', methods=['GET'])
def list_tasks():
    """
    列出所有任务
    """
    tasks = TaskManager().list_tasks()
    
    return jsonify({
        "success": True,
        "data": [t.to_dict() for t in tasks],
        "count": len(tasks)
    })


# ============== 图谱数据接口 ==============

@graph_bp.route('/data/<graph_id>', methods=['GET'])
def get_graph_data(graph_id: str):
    """
    获取图谱数据（节点和边）
    """
    try:
        builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
        graph_data = builder.get_graph_data(graph_id)
        
        return jsonify({
            "success": True,
            "data": graph_data
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@graph_bp.route('/delete/<graph_id>', methods=['DELETE'])
def delete_graph(graph_id: str):
    """
    删除Zep图谱
    """
    try:
        builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
        builder.delete_graph(graph_id)
        
        return jsonify({
            "success": True,
            "message": t('api.graphDeleted', id=graph_id)
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

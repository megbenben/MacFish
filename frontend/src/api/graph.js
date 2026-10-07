import service, { requestWithRetry } from './index'

/**
 * 生成本体（上传文档和模拟需求）
 * @param {Object} formData - 包含files, simulation_requirement, project_name等
 * @param {Function} [onProgress] - 上传进度回调，收到 0~100 的百分比
 * @returns {Promise}
 */
export function generateOntology(formData, onProgress) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/ontology/generate',
      method: 'post',
      data: formData,
      headers: {
        'Content-Type': 'multipart/form-data'
      },
      // 上传几十 MB 材料时界面此前只有一个转圈，用户不知道是卡住了还是在传
      onUploadProgress: onProgress
        ? (event) => {
            if (event.total) {
              onProgress(Math.round((event.loaded * 100) / event.total))
            }
          }
        : undefined
    })
  )
}

/**
 * 构建图谱
 * @param {Object} data - 包含project_id, graph_name等
 * @returns {Promise}
 */
export function buildGraph(data) {
  return requestWithRetry(() =>
    service({
      url: '/api/graph/build',
      method: 'post',
      data
    })
  )
}

/**
 * 查询任务状态
 * @param {String} taskId - 任务ID
 * @returns {Promise}
 */
export function getTaskStatus(taskId) {
  return service({
    url: `/api/graph/task/${taskId}`,
    method: 'get'
  })
}

/**
 * 获取图谱数据
 * @param {String} graphId - 图谱ID
 * @returns {Promise}
 */
export function getGraphData(graphId) {
  return service({
    url: `/api/graph/data/${graphId}`,
    method: 'get'
  })
}

/**
 * 获取项目信息
 * @param {String} projectId - 项目ID
 * @returns {Promise}
 */
export function getProject(projectId) {
  return service({
    url: `/api/graph/project/${projectId}`,
    method: 'get'
  })
}

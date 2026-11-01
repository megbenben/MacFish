import service from './index'

/** 冥想盆总览：计数与可收割来源 */
export const getPensieveOverview = () => service.get('/api/pensieve/overview')

/** 把已有项目/模拟/报告收存为记忆（幂等） */
export const harvestMemories = (data) => service.post('/api/pensieve/harvest', data)

/** 检索记忆 */
export const getMemories = (params = {}) =>
  service.get('/api/pensieve/memories', { params })

/** 记忆详情 + 相连丝线 */
export const getMemoryDetail = (memoryId) =>
  service.get(`/api/pensieve/memories/${memoryId}`)

/** 删除一条记忆 */
export const deleteMemory = (memoryId) =>
  service.delete(`/api/pensieve/memories/${memoryId}`)

/** 入盆回放 */
export const replayMemory = (memoryId) =>
  service.post('/api/pensieve/replay', { memory_id: memoryId })

/** 多条记忆蒸馏成记忆瓶 */
export const distillMemories = (memoryIds, question = '') =>
  service.post('/api/pensieve/distill', { memory_ids: memoryIds, question })

/** 记忆瓶列表 */
export const getVials = (limit = 50) =>
  service.get('/api/pensieve/vials', { params: { limit } })

/** 单个记忆瓶 */
export const getVial = (vialId) => service.get(`/api/pensieve/vials/${vialId}`)

/** 删除记忆瓶 */
export const deleteVial = (vialId) =>
  service.delete(`/api/pensieve/vials/${vialId}`)

/** 银色丝线网络 */
export const getStrandNetwork = (params = {}) =>
  service.get('/api/pensieve/network', { params })

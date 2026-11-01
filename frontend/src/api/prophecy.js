import service from './index'

/** 预言厅总览：统计 + 可铸造报告 */
export const getProphecyOverview = () => service.get('/api/prophecy/overview')

/** 从一份报告铸造预言球（同一报告幂等） */
export const forgeOrb = (reportId) =>
  service.post('/api/prophecy/orbs/forge', { report_id: reportId })

/** 到期提醒：即将到期 / 已过期的预言球 */
export const getProphecyReminders = (days = 7) =>
  service.get('/api/prophecy/reminders', { params: { days } })

/** 预言球列表 */
export const getOrbs = (params = {}) =>
  service.get('/api/prophecy/orbs', { params })

/** 单颗预言球（含问卜记录） */
export const getOrb = (orbId) => service.get(`/api/prophecy/orbs/${orbId}`)

/** 触碰开启 */
export const openOrb = (orbId) =>
  service.post(`/api/prophecy/orbs/${orbId}/open`)

/** 问卜 */
export const scryOrb = (orbId, question) =>
  service.post(`/api/prophecy/orbs/${orbId}/scry`, { question })

/** 应验校验 */
export const verifyOrb = (orbId, data) =>
  service.post(`/api/prophecy/orbs/${orbId}/verify`, data)

/** 删除预言球 */
export const deleteOrb = (orbId) =>
  service.delete(`/api/prophecy/orbs/${orbId}`)

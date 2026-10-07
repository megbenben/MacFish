import axios from 'axios'
import i18n from '../i18n'

// 创建axios实例
// 默认走相对路径：dev 由 vite.config.js 的 server.proxy['/api'] 转发到 :5001。
// 写死绝对地址会让那份代理成为死配置，每个请求都跨域带一次预检，而且把可用性绑死在
// 「后端正好在本机 5001」上。需要跨源访问时用 VITE_API_BASE_URL 显式指定。
const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 300000, // 5分钟超时（本体生成可能需要较长时间）
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
service.interceptors.request.use(
  config => {
    config.headers['Accept-Language'] = i18n.global.locale.value
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器（容错重试机制）
service.interceptors.response.use(
  response => {
    const res = response.data
    
    // 如果返回的状态码不是success，则抛出错误
    if (!res.success && res.success !== undefined) {
      console.error('API Error:', res.error || res.message || 'Unknown error')
      return Promise.reject(new Error(res.error || res.message || 'Error'))
    }
    
    return res
  },
  error => {
    console.error('Response error:', error)

    // 处理超时
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      const timeoutError = new Error('Request timeout - server may still be processing')
      return Promise.reject(timeoutError)
    }

    // 处理网络错误
    if (error.message === 'Network Error') {
      console.error('Network error - please check your connection')
      return Promise.reject(error)
    }

    // 从后端响应中提取实际错误信息
    if (error.response && error.response.data) {
      const data = error.response.data
      const msg = data.error || data.message || error.message
      const fullError = new Error(msg)
      // 保留原始响应：调用方需要按状态码分支处理（例如 409 的“需要确认”里
      // 带着 node_count / referenced_simulations），只留一句 message 是不够的
      fullError.response = error.response
      fullError.status = error.response.status
      fullError.data = data
      if (data.traceback) {
        fullError.traceback = data.traceback
        console.error('Backend traceback:', data.traceback)
      }
      return Promise.reject(fullError)
    }

    return Promise.reject(error)
  }
)

// 带重试的请求函数
export const requestWithRetry = async (requestFn, maxRetries = 3, delay = 1000) => {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await requestFn()
    } catch (error) {
      // 4xx 是请求本身有问题（格式不支持、参数缺失、体积超限），重试不会变好。
      // 文件上传这类请求被重试还会把整个 multipart 重传一遍，代价很大。
      const status = error?.response?.status
      if (status >= 400 && status < 500) throw error

      if (i === maxRetries - 1) throw error

      console.warn(`Request failed, retrying (${i + 1}/${maxRetries})...`)
      await new Promise(resolve => setTimeout(resolve, delay * Math.pow(2, i)))
    }
  }
}

export default service

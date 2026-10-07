import { createI18n } from 'vue-i18n'
import languages from '../../../locales/languages.json'

const localeFiles = import.meta.glob('../../../locales/!(languages).json', { eager: true })

const messages = {}
const availableLocales = []

for (const path in localeFiles) {
  const key = path.match(/\/([^/]+)\.json$/)[1]
  if (languages[key]) {
    messages[key] = localeFiles[path].default
    availableLocales.push({ key, label: languages[key].label })
  } else {
    // locales/ 下有待翻译文件却没在 languages.json 里登记：跳过。
    // 反过来（登记了却没有文件）以前是完全静默的——切换器里悄悄少一种语言，
    // 所以在下面补一次告警，避免下次又对不上。
    console.warn(`[i18n] locales/${key}.json 未在 locales/languages.json 中登记，已忽略`)
  }
}

// 登记了却没有对应翻译文件的，明确告警（历史上声明了 7 种、实有 2 种）
for (const key in languages) {
  if (!messages[key]) {
    console.warn(`[i18n] locales/languages.json 声明了 "${key}"，但缺少 locales/${key}.json，该语言不会出现在切换器里`)
  }
}

const savedLocale = localStorage.getItem('locale') || 'zh'

const i18n = createI18n({
  legacy: false,
  locale: savedLocale,
  fallbackLocale: 'zh',
  messages
})

export { availableLocales }
export default i18n

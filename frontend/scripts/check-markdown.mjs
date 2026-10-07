/**
 * renderMarkdown 的行为与转义检查。
 *
 * 前端没有测试框架（package.json 里只有 vite），所以按 backend/scripts/test_multi_format.py
 * 的做法留一个断言脚本，改动渲染器后跑一遍即可：
 *
 *   node frontend/scripts/check-markdown.mjs
 *
 * 关注两件事：
 * 1) 常见的 Markdown 语法仍然渲染成原来的标签（回归）
 * 2) 文本里的 HTML 不会被原样注入（v-html 的注入面）
 */
import { renderMarkdown } from '../src/utils/markdown.js'

let passed = 0
let failed = 0

const check = (name, condition, detail) => {
  if (condition) {
    passed++
    console.log(`✓ ${name}`)
  } else {
    failed++
    console.log(`✗ ${name}`)
    if (detail !== undefined) console.log(`    实际: ${detail}`)
  }
}

console.log('--- 语法渲染 ---')

const renders = [
  ['粗体', '**粗**', '<strong>粗</strong>'],
  ['斜体', '*斜*', '<em>斜</em>'],
  ['行内代码', '用 `npm run dev` 启动', 'inline-code'],
  ['代码块', '```js\nconst a = 1\n```', 'code-block'],
  ['无序列表', '- 甲\n- 乙', 'md-ul'],
  ['有序列表', '1. 甲\n2. 乙', 'md-ol'],
  ['引用块', '> 引用内容', 'md-quote'],
  ['分隔线', '---', 'md-hr'],
  ['小节标题', '正文\n\n## 小节\n\n更多', 'md-h3'],
]

for (const [name, input, needle] of renders) {
  const out = renderMarkdown(input)
  check(name, out.includes(needle), out.slice(0, 140))
}

// 章节开头那个二级标题会被去掉，因为外层已经显示了章节标题
const stripped = renderMarkdown('## 章节标题\n\n正文')
check('开头的二级标题被去掉', !stripped.includes('章节标题') && stripped.includes('正文'), stripped)

console.log('--- 转义 ---')

check('<script> 不原样出现', !renderMarkdown('<script>alert(1)</script>').includes('<script'))
check(
  '实体编码不能绕过',
  !renderMarkdown('&lt;img src=x onerror=alert(1)&gt;').includes('<img'),
  renderMarkdown('&lt;img src=x onerror=alert(1)&gt;')
)
check('& 被转义', renderMarkdown('A & B').includes('&amp;'))
check('< 被转义', renderMarkdown('a < b').includes('&lt;'))
// 关键：`>` 是引用块的语法前缀，不能被转义掉
check('> 保持原样，引用块仍然生效', renderMarkdown('> q').includes('<blockquote'))
check('引号照常显示', renderMarkdown('他说"你好"').includes('"你好"'))

console.log('--- 边界 ---')

check('空输入返回空串', renderMarkdown('') === '' && renderMarkdown(null) === '')
check('普通段落被包起来', renderMarkdown('一句话').includes('md-p'))

console.log(`\n通过 ${passed} / ${passed + failed}`)
process.exit(failed === 0 ? 0 : 1)

# patches/

本目录存放**与 MacFish 项目本身无关**的、部署环境所需的补丁备份。

---

## openclaw-128515-hotfix.patch

### 为什么需要它

OpenClaw 有一个上游 bug:[**openclaw/openclaw#128515**](https://github.com/openclaw/openclaw/issues/128515)
— *Config publication does not refresh prepared model owners*。

机制:Gateway 启动时会缓存一个 *prepared model owner*,它与**整个配置的哈希**绑定。
之后任何一次配置热重载都会发布新的运行时配置,但**不刷新**这个 owner。只读的消费者
(聊天渠道的回复派发)拿新配置去比对旧哈希 → 不匹配 → 抛
`PreparedModelCatalogConfigReplacedError` → **每一条消息都回复失败**。

关键特征:**重启无法解决**。冷启动后第一条消息照样失败,因为 channel 启动时自己会写
配置(如 `channels.<id>.channelConfigUpdatedAt`),进程一 `ready` 就处于不匹配状态。

官方修复(#128608 等)已合入 main,但**尚未进入任何发布版本** —— 截至 2026-09-13,
`npm dist-tags` 里 `latest` 和 `beta` 都是 `2026.9.4`,没有更新的版本可用。

### 这个补丁做了什么

只改一个函数,位于安装目录的
`dist/prepared-model-catalog-B1j3jtLv.mjs`:

```js
// 改前
function acceptsPreparedSnapshotConfig(snapshot, input, policy) {
	return policy === "published" || preparedModelRuntimeConfigsMatch(snapshot.config, input.config);
}
```

```js
// 改后:始终接受
function acceptsPreparedSnapshotConfig(snapshot, input, policy) {
	/* ...注释... */
	return true;
}
```

`"published"` 策略本来就容忍这种漂移(见原代码同一行的 `||`),这个补丁只是让严格的
`"exact"` 策略也一并容忍。**因此唯一的行为变化是:`"exact"` 不再抛
`PreparedModelCatalogConfigReplacedError`。**

### 实测效果

| 指标 | 打补丁前 | 打补丁后 |
|---|---|---|
| `Embedded agent failed before reply` | **29 次** | **0 次** |
| 端到端回复送达(`outbound: text sent OK`) | **0 次** | **6 次连续成功** |

### 应用方式

```bash
# 1. 备份原文件
cp /opt/homebrew/lib/node_modules/openclaw/dist/prepared-model-catalog-B1j3jtLv.mjs \
   ~/prepared-model-catalog-B1j3jtLv.mjs.orig

# 2. 应用补丁(注意 dist 文件名可能随版本变化)
cd /opt/homebrew/lib/node_modules/openclaw/dist
patch -p1 < ~/MacFish/patches/openclaw-128515-hotfix.patch

# 3. 必须先做语法检查 —— 这里出错会让整个 gateway 起不来
node --check prepared-model-catalog-B1j3jtLv.mjs

# 4. 重启
openclaw gateway restart
```

### 回滚

```bash
cd /opt/homebrew/lib/node_modules/openclaw/dist
patch -R -p1 < ~/MacFish/patches/openclaw-128515-hotfix.patch
node --check prepared-model-catalog-B1j3jtLv.mjs
openclaw gateway restart
```

### ⚠️ 重要限制

1. **升级 openclaw 会覆盖此补丁。** 每次 `brew upgrade openclaw` 或 `npm i -g openclaw`
   之后都要重新应用。升级后先判断还需不需要:
   ```bash
   grep -c 'Embedded agent failed' ~/Library/Logs/openclaw/gateway.log
   ```
   如果是 `0` 且官方修复已进 release,**就别再打这个补丁了**。

2. **文件名带哈希**(`prepared-model-catalog-B1j3jtLv.mjs`),不同版本会变。升级后需要
   按内容重新定位那个函数,而不是照搬文件名。

3. **这个补丁只是「容忍」陈旧 owner,不是「修复」。** 官方修复(#128608)会真正刷新
   owner;本补丁让消费者继续使用上一代的 catalog。对本机场景(模型清单稳定)够用,
   但不是正确解法 —— **一旦官方修复进 release,应当立即弃用本补丁。**

4. 排查时的一个陷阱:`outcome=success` **在成功时也不会出现在 gateway 日志里**。
   可靠的出站信号是插件日志中的 `outbound: text sent OK`
   (`/tmp/openclaw/openclaw-<date>.log`)。只 grep `outcome=` 会把正常工作的渠道误判为死掉。

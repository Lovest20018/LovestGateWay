# 界面截图

README 使用真实前端源码和完全虚构的演示数据生成截图，不连接运行中的网关。节点名称、统计值、版本显示及文档示例地址均不代表真实部署或性能结果。

- `overview.png`：桌面概览，亮色。
- `proxies.png`：桌面代理页面，亮色。
- `mobile-dark.png`：390px 手机代理页面，暗色。

## 更新截图

需要 Node.js、Playwright 和本机 Chrome。先安装 Playwright，或将 `PPGW_PLAYWRIGHT_MODULE` 指向已有 Playwright 模块，然后在项目根目录运行：

```sh
node frontend/build.mjs --output dist/docs-ui
node scripts/capture-doc-screenshots.cjs dist/docs-ui docs/images
```

渲染脚本拦截所有请求，只返回脚本内演示数据和本地构建文件；遇到未定义接口、外部地址或页面错误会失败。公开前逐张检查图片内容，避免用真实部署截图替换演示数据。

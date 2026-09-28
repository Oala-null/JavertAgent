# 249 工作台加载优化部署

交付目录：`JiaZhongXin/Releases/Javert-249-Web-performance-v1`，默认不生成 ZIP。

完整安装与回滚步骤见 [补丁说明](../delivery/web_performance/README_zh.md)，测试与已知边界见 [验收记录](../delivery/web_performance/VALIDATION.md)。现场上传整个版本目录即可执行；不要只复制单个 Python/JS 文件。

必须成组合并/交付的运行文件：

| 文件 | 改动 |
|---|---|
| `src/javert/store/sqlserver_store.py` | 历史大字段只返回最新主卡，最新锚点，稳定时间排序 |
| `src/javert/web/api/routes_workbench.py` | 患者首屏不查全局侧栏；登录保护的摘要分页、筛选、排序；慢查询缓存计时 |
| `src/javert/web/static/app.js` | 独立加载侧栏、50 卡分页、防抖、取消旧请求、失败重试、原文请求去重 |
| `src/javert/web/templates/_sidebar.html` | 分页侧栏框架；保留上海主诊/费用隔离 |
| `src/javert/web/templates/_patient_cards.html` | 服务端每页卡片模板，必须随路由一起安装 |

不包含 FP8 分支新增的模型、肿瘤知识库或 2C 契约。原 v1 的 `deploy/249/known-release.json` 不改写，新目录提供 `known-release-after.json` 验证叠加后的完整磁盘身份。

维护者从已提交树构建：

```bash
python3 scripts/build_249_web_performance.py JiaZhongXin/Releases
```

目标目录存在即拒绝覆盖。新代码若已改变 v1 清单中的 payload 或依赖，构建拒绝，必须另立版本。`DEPLOY_COMMIT` 保留旧基线；新包以 `manifest.json` 的源码提交和文件摘要标识。医院是否已安装、Web 是否已重启需现场验收，本次本地交付不作这一声明。

# 冻结v1维护材料

本目录的installer.py、install.sh、README_zh.md、START_HERE.txt、VALIDATION.md来自已交付v1；描述的是该交付时点，不随当前现场状态改写。当前操作入口和证据以[运维手册](../../docs/deployment_243_gnome.md)和[核验报告](../../docs/243_249_hotfix_qa.md)为准。

- `v1-manifest.json`固定已交付10文件及4依赖摘要。
- `baseline-code.json.gz`只包含安装前受控源码，用于离线回滚测试；每个文件已匹配manifest的before/依赖摘要，没有配置或患者数据。
- 根目录`scripts/build_243_lis_hotfix.py`只接受仍与v1摘要相同的源码，且拒绝覆盖已存在交付目录。
- 原交付ZIP保持不变。新业务逻辑必须新版本，不修改v1摘要来掩盖漂移。

这里的新增维护文档、代码夹具及manifest不是要覆盖到正在运行应用目录的文件。

# 自研模型路线（InteractFormer 原型）

原仓库确实已有自研架构代码：`interactformer/interaction/`（S1）、`background/`（S2）、`bridge/`（Streaming Context Bridge）、`orchestrator/`（调度）、`codec/` 与 `utils/`。`configs/model_config.yaml` 和 `tests/` 中保留了对应配置与原型测试。

这些文件从原仓库**原样复制**，没有改动模型实现。完整包中也保留了原有 MiniCPM 适配与兼容模块，因为它们本来就与架构代码同属一个 Python 包；这不代表自研模型已经依赖或确定使用 MiniCPM。MiniCPM 目录中的原文件仍保持不变，以免破坏已能运行的路径。

当前代码是架构原型，不等于已有可部署的自研模型权重或已确定的模型接口。Bridge 仍是理想设计；在模型契约明确前，不进行新的 Bridge 适配，也不声称该路线已经跑通。

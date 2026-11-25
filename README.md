# 闲兔数字人工作台 V3

独立插件目录：`ComfyUI-Digital-Human-V3`。版本：`0.1.2`。
主节点：`XiantuDigitalHumanStudioV3`（闲兔｜数字人工作台 V3）。

提供音频编辑、分镜切分、参考图、原始及优化后提示词、连续生成、停止、生成记录、放大前后文件管理和合并。
本地 GGUF 优化实现及 Windows Vulkan llama.cpp b10689 运行文件、依赖 DLL 和许可证随完整插件提供；模型权重单独安装。

## 安装

1. 将完整 `ComfyUI-Digital-Human-V3` 文件夹放入 `ComfyUI/custom_nodes/`，文件夹内应直接包含 `__init__.py`、`nodes.py` 和 `plugin_manifest.json`。
2. 使用该 ComfyUI 自己的 Python 安装 `requirements.txt` 中尚缺的依赖。
3. 重启 ComfyUI，刷新浏览器，打开提供的 H3 数字人 V3 工作流。

仅更新时也应覆盖完整插件文件夹。保留 `runtime/`、`web/`、`integrity/`、`tools/`、许可证及发布配置。
用户项目与生成视频位于 `output/`，不在插件源码文件夹内。

## H3 工作流连接

输入：`H3_CLIP`、`H3_VIDEO_VAE`、`H3_AUDIO_VAE`。
输出顺序：`H3条件`、`H3 Latent`、`H3图像`、`H3音频`、`H3放大前文件名`、`H3放大后文件名`。
模型加载、采样、解码与保存由画布上的工作流节点连接。本插件不调用其他导演台插件的实现。

当前配套的 Pika 6 步、RelayVSR 工作流另使用 `ComfyUI-KJNodes`、`minimax-h3-chunk-star7` 和 `ComfyUI-Xiantu-VSR-Stream-Save`。
RelayVSR 条件、基础采样和视频节点来自 ComfyUI 内核。插件文件夹中的历史测试模板保留原样；网盘根目录的主工作流为当前保存版本。

当前主工作流的模型目录：

```text
models/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors
models/diffusion_models/relayvsr_x4_int8_convrot.safetensors
models/clip/qwen3vl_32b_minimax_h3_int8_convrot.safetensors
models/loras/pika_h3_flex_ref2va_v1_bf16.safetensors
models/vae/minimax_h3_video_vae_fp16.safetensors
models/vae/minimax_h3_audio_vae_fp32.safetensors
models/vae/relayvsr_flash_vae_x4_fp32.safetensors
models/vae_approx/taeh3.safetensors
```

忽略的 2 倍分支另外使用 `relayvsr_x2_lightx2v_int8_convrot.safetensors` 和 `relayvsr_flash_vae_x2_fp32.safetensors`。
语言模型和匹配的视觉投影 GGUF 放在 `models/LLM/`（可含子目录）；只在使用本地提示词优化时加载。

## 项目导入与导出

点击“导出项目”生成 ZIP，带上分镜、原始及优化后提示词、当前绑定参考图、原音频、项目设置及节点/组标记。
点击“导入项目”选择完整 ZIP，恢复为独立新项目。原项目与已有视频保留。旧版分镜 JSON 继续支持导入。
ZIP 中的 `分镜提示词.csv` 用于查看；恢复全部素材及设置时选择 ZIP。
生成视频和未应用的公共资产库素材不装入项目包。标记按当前工作流内匹配的节点恢复。

## 保存位置

- 项目：`output/XiantuDigitalHumanV3/projects/`
- 放大前：`output/数字人V3/放大前/`
- 放大后：`output/数字人V3/放大后/`
- 合并：`output/数字人V3/合并成品/`

## 独立性与完整性

仅注册 V3 节点和 `/xiantu/dhv3` 接口，前端状态、本地语言模型进程及功能标记均有独立作用域。
公共资产库通过本插件自己的文件读写实现互通，不依赖助手、漫剧、角色替换或全功能导演台代码。
使用 ComfyUI 的 Python 执行 `tools/verify_integrity.py`，可核验随包文件的 SHA256。

## 发布与更新配置

发布仓库配置：`https://github.com/xiantuhenlan/ComfyUI-Digital-Human-V3`。
更新清单配置：`https://raw.githubusercontent.com/xiantuhenlan/ComfyUI-Digital-Human-V3/main/plugin_manifest.json`。
ComfyUI 包名：`comfyui-digital-human-v3`；发布者：`xiantuhenlan`。

当前发布版本：`0.1.2`。GitHub 仓库、Release 和更新清单使用上述固定地址。
GitHub 发布与 ComfyUI Registry 审核分别回读确认；审核状态以 Registry 实际记录为准。

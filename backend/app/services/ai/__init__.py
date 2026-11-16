"""AI 相关服务（LangChain 时代遗留目录，现只剩 token_usage）。

对话链与模型调用已经整体搬到 ``ai-service/``（pi-agent 运行时），这里只保留
一次生成类任务（AI 生成 / 导入）需要的 token 归集与计费换算。

不要往这个目录加新逻辑：新增 AI 能力走 ``ai-service/`` + ``api/ai.py`` 代理。
目录保留是因为 ``token_usage`` 被计费链路直接引用，挪到 ``services/`` 根下只会
让引用路径来回改。
"""

"""platforms: 各平台专属代码。

每平台一个子包(temu/、alibaba1688/...),含:
- adapter.py   raw_json → 统一 Product 模型
- pipeline.py  该平台的流程编排(调哪些 step、什么顺序)
- export.py    该平台的导出模板(xlsx 等)
- router.py    该平台的 HTTP 路由(FastAPI APIRouter)
- prompts/     该平台专属的提示词
"""

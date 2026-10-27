"""Football Workbench (fwb): 五大联赛数据管道 + 分析层。

分层：
- fwb.sources   数据获取（每个数据源一个模块，只负责"拿到并规范化"）
- fwb.storage   Parquet 分区存储 + manifest（数据版本与水位线）
- fwb.pipeline  ETL 编排：增量 / 全量 / StatsBomb 回填 + 质量门
- fwb.analytics 衍生指标与模型（只读原始数据，输出衍生表）
- fwb.app       Streamlit 端的数据加载与公共组件
"""

__version__ = "1.0.0"

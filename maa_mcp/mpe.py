import json
import webbrowser
from pathlib import Path

from lzstring import LZString

from maa_mcp.core import mcp

# MPE 分享协议版本
SHARE_VERSION = 1
# URL 参数名
SHARE_PARAM = "shared"
# 默认 MPE 基准地址
DEFAULT_MPE_BASE_URL = "https://mpe.codax.site/stable"
# URL 最大大小限制
MAX_URL_SIZE = 60 * 1024  # 60KB


def generate_share_link(
    pipeline_obj: dict, base_url: str = DEFAULT_MPE_BASE_URL
) -> str:
    # 生成分享链接
    payload = {
        "v": SHARE_VERSION,
        "d": pipeline_obj,
    }
    json_string = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    lz = LZString()
    compressed = lz.compressToEncodedURIComponent(json_string)
    share_url = f"{base_url}?{SHARE_PARAM}={compressed}"
    return share_url


@mcp.tool(
    name="open_pipeline_in_mpe",
    description="""
    将 Pipeline JSON 文件生成 MPE（MaaPipelineEditor）可视化链接并在浏览器中打开。

    参数：
    - pipeline_file_path: Pipeline JSON 文件的本地路径（字符串）
    - base_url: MPE 基准地址（可选），默认为 "https://mpe.codax.site/stable"

    功能说明：
    该工具会读取指定路径的 Pipeline JSON 文件，将数据压缩编码后生成一个分享链接，
    并自动在系统默认浏览器中打开，方便用户可视化查看工作流结构。

    注意：
    - 此工具无返回值，仅执行打开浏览器的操作
    - 仅在用户要求查看 Pipeline 流程图时生成并打开分享链接
    - 传入的文件路径必须指向一个有效的本地 JSON 文件
    - 如果生成的 URL 超过 60KB，将返回错误提示而不打开浏览器
    """,
)
def open_pipeline_in_mpe(
    pipeline_file_path: str,
    base_url: str = DEFAULT_MPE_BASE_URL,
) -> None:
    # 读取文件内容
    file_path = Path(pipeline_file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Pipeline 文件不存在: {pipeline_file_path}")
    if not file_path.is_file():
        raise ValueError(f"路径不是文件: {pipeline_file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        pipeline_obj = json.load(f)

    # 生成分享链接
    share_url = generate_share_link(pipeline_obj, base_url)

    # 检查 URL 大小
    url_size = len(share_url.encode("utf-8"))
    if url_size > MAX_URL_SIZE:
        size_kb = url_size / 1024
        raise ValueError(
            f"生成的分享链接过大（{size_kb:.2f} KB），请自行通过复制或文件的方式导入 Pipeline 至 MPE。"
        )

    webbrowser.open(share_url)

"""
OCR 资源自动下载和解压模块
"""
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

from maa_mcp.core import mcp
from maa_mcp.paths import get_ocr_dir, get_model_dir

# OCR 资源下载地址
OCR_DOWNLOAD_URL = "https://download.maafw.xyz/MaaCommonAssets/OCR/ppocr_v5/ppocr_v5-zh_cn.zip"

# OCR 模型所需文件列表
OCR_REQUIRED_FILES = ["det.onnx", "keys.txt", "rec.onnx"]


def _log(log_file: Path, message: str):
    """写入日志文件"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def check_ocr_files_exist(ocr_dir: Path | None = None) -> bool:
    """
    检查 OCR 模型文件是否完整存在
    
    Args:
        ocr_dir: OCR 资源目录，默认为跨平台用户数据目录
    
    Returns:
        如果所有必需文件都存在返回 True，否则返回 False
    """
    if ocr_dir is None:
        ocr_dir = get_ocr_dir()
    
    return all((ocr_dir / f).exists() for f in OCR_REQUIRED_FILES)


def download_and_extract_ocr(ocr_dir: Path | None = None) -> bool:
    """
    下载并解压 OCR 资源文件
    
    Args:
        ocr_dir: OCR 资源目标目录，默认为跨平台用户数据目录
    
    Returns:
        下载解压成功返回 True，失败返回 False
    """
    if ocr_dir is None:
        ocr_dir = get_ocr_dir()
    
    # 确保目标目录存在
    ocr_dir.mkdir(parents=True, exist_ok=True)
    
    # zip 文件和日志文件保存到 model 目录（ocr 的上级目录）
    model_dir = get_model_dir()
    model_dir.mkdir(parents=True, exist_ok=True)
    zip_file = model_dir / "ppocr_v5-zh_cn.zip"
    log_file = model_dir / "download.log"
    
    try:
        _log(log_file, f"开始下载 OCR 资源文件")
        _log(log_file, f"下载地址: {OCR_DOWNLOAD_URL}")
        
        request = Request(OCR_DOWNLOAD_URL, headers={"User-Agent": "MaaMCP/1.0"})
        
        with urlopen(request, timeout=300) as response:
            total_size = response.headers.get("Content-Length")
            total_size = int(total_size) if total_size else None
            
            downloaded = 0
            chunk_size = 65536
            last_percent = -1
            
            with open(zip_file, "wb") as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if total_size:
                        percent = int((downloaded / total_size) * 100)
                        if percent >= last_percent + 10:
                            last_percent = percent
                            mb_downloaded = downloaded / (1024 * 1024)
                            mb_total = total_size / (1024 * 1024)
                            _log(log_file, f"下载进度: {mb_downloaded:.1f}/{mb_total:.1f} MB ({percent}%)")
        
        _log(log_file, "下载完成，正在解压...")
        
        with zipfile.ZipFile(zip_file, "r") as zip_ref:
            temp_extract_dir = ocr_dir / "_temp_extract"
            temp_extract_dir.mkdir(exist_ok=True)
            zip_ref.extractall(temp_extract_dir)
            
            for required_file in OCR_REQUIRED_FILES:
                found = False
                for file_path in temp_extract_dir.rglob(required_file):
                    dest_file = ocr_dir / required_file
                    if dest_file.exists():
                        dest_file.unlink()
                    shutil.move(str(file_path), str(dest_file))
                    found = True
                    break
                
                if not found:
                    _log(log_file, f"警告: 未在压缩包中找到 {required_file}")
            
            shutil.rmtree(temp_extract_dir, ignore_errors=True)
        
        _log(log_file, f"解压完成，OCR 资源已保存到: {ocr_dir}")
        return True
        
    except URLError as e:
        _log(log_file, f"下载失败: {e}")
        return False
    except zipfile.BadZipFile:
        _log(log_file, "解压失败: 下载的文件不是有效的 ZIP 文件")
        return False
    except Exception as e:
        _log(log_file, f"发生错误: {e}")
        return False


def ensure_ocr_resources(ocr_dir: Path | None = None) -> bool:
    """
    确保 OCR 资源文件存在，如果不存在则自动下载
    
    Args:
        ocr_dir: OCR 资源目录，默认为跨平台用户数据目录
    
    Returns:
        资源可用返回 True，否则返回 False
    """
    if ocr_dir is None:
        ocr_dir = get_ocr_dir()
    
    if check_ocr_files_exist(ocr_dir):
        return True
    
    return download_and_extract_ocr(ocr_dir)


@mcp.tool(
    name="check_and_download_ocr",
    description="""
    检查 OCR 模型文件是否存在，如果不存在则从网络下载。

    参数：
    - resource_path: 资源包根目录路径（字符串），可选
      - 如果不传，默认使用跨平台用户数据目录

    返回值：
    - 成功：返回包含状态信息的字符串（资源已存在或下载成功）
    - 失败：返回错误信息字符串

    说明：
    首次使用时，当调用 ocr() 函数返回 "OCR 模型文件不存在" 的提示时，需要调用此函数下载 OCR 资源。
    下载完成后再重新调用 ocr() 即可正常使用。后续使用无需再次下载。
""",
)
def check_and_download_ocr(resource_path: str | None = None) -> str:
    if resource_path:
        ocr_dir = Path(resource_path) / "model" / "ocr"
    else:
        ocr_dir = get_ocr_dir()
    
    if check_ocr_files_exist(ocr_dir):
        return f"OCR 模型文件已存在: {ocr_dir}"
    
    if download_and_extract_ocr(ocr_dir):
        return f"OCR 模型文件下载成功: {ocr_dir}"
    else:
        return f"OCR 模型文件下载失败，请检查网络连接或手动下载，日志文件: {get_model_dir() / 'download.log'}"


if __name__ == "__main__":
    ensure_ocr_resources()

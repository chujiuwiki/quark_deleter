# quark_deleter_unified.py

import requests
import time
import json
import re 
import logging

# --- 1. 配置区域 ---
COOKIE = "请在这里粘贴你的夸克网盘COOKIE字符串"
TARGET_FOLDER_ID = "ae4e38ff3c6d4ac989a981013d61a2ea" # 你的目标文件夹ID
# !!! 以上配置项务必修改 !!!

DELETE_OLDER_THAN_SECONDS = 1 * 60 * 60  # 1小时
POLL_INTERVAL_SECONDS = 5 * 60  # 5分钟

# --- 日志配置 ---
logging.basicConfig(
    level=logging.DEBUG, # 设置为DEBUG以获取详细日志
    format='%(asctime)s - %(levelname)s - %(funcName)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        # logging.FileHandler("quark_deleter.log", encoding="utf-8")
    ]
)

# --- 夸克API相关常量 ---
QUARK_API_BASE_URL_DRIVE_PC = "https://drive-pc.quark.cn"
LIST_FILES_ENDPOINT = "/1/clouddrive/file/sort"
DELETE_FILES_ENDPOINT = "/1/clouddrive/file/delete"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
GENERAL_REFERER = "https://pan.quark.cn/"

# --- 2. 核心函数 ---

def get_stoken_from_cookie(cookie_str):
    if not cookie_str:
        return None
    match = re.search(r'stoken=([^;]+)', cookie_str)
    if match:
        stoken_value = match.group(1)
        logging.info(f"成功从Cookie中提取到stoken: {stoken_value[:10]}...")
        return stoken_value
    return None

def _make_request(method, url, headers, params=None, json_data=None, data=None, timeout=30):
    try:
        if json_data:
            response = requests.request(method, url, headers=headers, params=params, json=json_data, timeout=timeout)
        else:
            response = requests.request(method, url, headers=headers, params=params, data=data, timeout=timeout)
        
        response.raise_for_status()
        try:
            return response.json()
        except json.JSONDecodeError:
            if response.text == "":
                logging.info(f"请求成功，但响应体为空。URL: {url}")
                return {"code": 0, "status": 200, "message": "Response body is empty but request was successful"}
            logging.error(f"解析JSON响应失败，URL: {url}, 响应: {response.text}")
            return None
            
    except requests.exceptions.HTTPError as errh:
        logging.error(f"HTTP错误: {errh.response.status_code} {errh.response.reason} for url: {errh.response.url}")
        if response is not None:
            logging.error(f"HTTP错误响应内容: {response.text}")
    except requests.exceptions.ConnectionError as errc:
        logging.error(f"网络连接错误: {errc}")
    except requests.exceptions.Timeout as errt:
        logging.error(f"请求超时: {errt}")
    except requests.exceptions.RequestException as err:
        logging.error(f"请求发生未知错误: {err}")
    return None


def list_all_items_in_folder(cookie_str, folder_id): # 函数名修改以反映其行为
    """获取指定夸克网盘文件夹内的所有条目（文件和文件夹），并实现分页。"""
    all_items = []
    current_page = 1
    items_per_page = 50 

    logging.info(f"开始分页列出文件夹 [{folder_id}] 内的所有条目...")

    while True:
        url = f"{QUARK_API_BASE_URL_DRIVE_PC}{LIST_FILES_ENDPOINT}"
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": GENERAL_REFERER,
            "Cookie": cookie_str,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://pan.quark.cn",
        }
        params = {
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
            "pdir_fid": folder_id,
            "_page": current_page,
            "_size": items_per_page,
            "_fetch_total": 1, 
            "_fetch_sub_dirs": 0,
            "_sort": "created_at:asc", # 按创建时间升序
        }

        logging.info(f"正在获取第 {current_page} 页条目 (每页 {items_per_page} 个)...")
        response_data = _make_request("GET", url, headers=headers, params=params)

        page_items_processed_count = 0
        if response_data:
            if response_data.get('code') == 0 and response_data.get('status') == 200:
                data_node = response_data.get('data', {})
                if data_node is None:
                    logging.warning(f"第 {current_page} 页API响应中 'data' 字段为 null。")
                    break 

                data_list = data_node.get('list', [])
                metadata = response_data.get('metadata', {})
                current_page_item_count_from_api = metadata.get('_count', len(data_list)) 
                total_items_from_api = metadata.get('_total')

                if not data_list and current_page_item_count_from_api == 0:
                    logging.info(f"第 {current_page} 页没有条目数据，分页结束。")
                    break

                for item in data_list:
                    item_type = "文件夹" if item.get('dir') is True else ("文件" if item.get('dir') is False else "未知类型")
                    
                    created_at_ms = item.get('created_at')
                    # 对于文件夹，l_created_at 可能更代表其“元数据”创建时间，但created_at通常是用户操作时间
                    # 如果要严格按“转存时间”，created_at 应该更准
                    timestamp_to_use_ms = created_at_ms # 优先使用 created_at

                    if timestamp_to_use_ms is None: # 如果 created_at 为空，尝试其他
                        timestamp_to_use_ms = item.get('l_created_at')
                        if timestamp_to_use_ms is None:
                            timestamp_to_use_ms = item.get('operated_at') # 再尝试 operated_at
                            if timestamp_to_use_ms is None:
                                timestamp_to_use_ms = item.get('updated_at') # 最后尝试 updated_at
                    
                    if timestamp_to_use_ms is None: # 如果所有常用毫秒时间戳都为空
                        itime_s = item.get('itime') # 尝试秒级 itime
                        if itime_s is not None:
                            try:
                                timestamp_to_use_ms = int(itime_s) * 1000
                                logging.debug(f"条目 '{item.get('file_name')}' 使用 itime_s (转换为ms): {timestamp_to_use_ms}")
                            except ValueError:
                                logging.warning(f"条目 '{item.get('file_name')}' 的 itime_s 格式无效: {itime_s}, 跳过。")
                                continue
                        else:
                            logging.warning(f"条目 '{item.get('file_name')}' 缺少所有已知的时间戳字段，已跳过。")
                            continue
                    
                    item_info = {
                        "fid": item.get('fid'),
                        "file_name": item.get('file_name'),
                        "itime_ms": timestamp_to_use_ms,
                        "type": item_type, # 添加类型信息
                    }

                    if all(value is not None for key, value in item_info.items() if key != 'type'): # type可以是未知
                        all_items.append(item_info)
                        page_items_processed_count +=1
                    else:
                        logging.warning(f"发现不完整的重要条目信息，已跳过: {item.get('file_name')}")
                
                logging.info(f"第 {current_page} 页获取到 {current_page_item_count_from_api} 个原始条目，处理了 {page_items_processed_count} 个有效条目。")
                
                if total_items_from_api is not None:
                    logging.info(f"已累计处理 {len(all_items)} 个条目，API报告总条目数: {total_items_from_api}。")
                    if current_page * items_per_page >= total_items_from_api :
                         logging.info(f"已获取所有页数据 (基于_total: {total_items_from_api})。")
                         break
                elif current_page_item_count_from_api < items_per_page:
                    logging.info(f"当前页返回条目 ({current_page_item_count_from_api}) 少于请求条目 ({items_per_page})，分页结束。")
                    break
                
                current_page += 1
                time.sleep(0.5) 

            else: 
                err_msg = response_data.get('message', '未知API错误')
                logging.error(f"获取第 {current_page} 页条目失败: {err_msg}")
                break 
        else: 
            logging.error(f"请求第 {current_page} 页条目时发生网络或解析错误，终止分页。")
            break
            
    logging.info(f"全部分页获取完成，总共获取到 {len(all_items)} 个有效条目。")
    return all_items


def delete_quark_items(cookie_str, item_fids_to_delete): # 函数名修改
    """删除夸克网盘中的指定条目（文件或文件夹）。"""
    if not item_fids_to_delete:
        logging.info("没有需要删除的条目。")
        return True

    url = f"{QUARK_API_BASE_URL_DRIVE_PC}{DELETE_FILES_ENDPOINT}"
    params = { "pr": "ucpro", "fr": "pc", "uc_param_str": "" }
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Referer": GENERAL_REFERER,
        "Cookie": cookie_str,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://pan.quark.cn",
    }
    json_payload = {
        "action_type": 2, # 2 代表删除
        "filelist": item_fids_to_delete, # API似乎不区分文件或文件夹列表，都用filelist
        "exclude_fids": []
    }

    logging.info(f"准备删除 {len(item_fids_to_delete)} 个条目: {', '.join(item_fids_to_delete)}")
    response_data = _make_request("POST", url, headers=headers, params=params, json_data=json_payload)

    if response_data:
        if response_data.get('code') == 0 and response_data.get('status') == 200:
            task_id = response_data.get('data', {}).get('task_id')
            logging.info(f"成功发起删除请求，任务ID: {task_id if task_id else 'N/A'}。条目ID: {', '.join(item_fids_to_delete)}")
            return True
        else:
            err_msg = response_data.get('message', '未知API错误')
            logging.error(f"删除条目失败: {err_msg} (代码: {response_data.get('code')}, 状态: {response_data.get('status')})")
            return False
    return False

# --- 3. 主逻辑循环 ---
def main_loop():
    logging.info("夸克网盘自动清理程序已启动 (统一删除逻辑)。")
    # ... (其他日志和配置检查不变) ...
    if "请在这里粘贴你的夸克网盘COOKIE字符串" in COOKIE or COOKIE.strip() == "":
        logging.error("错误：COOKIE 未配置，请修改脚本中的 COOKIE 常量。程序退出。")
        return
    if TARGET_FOLDER_ID.strip() == "": # 简单检查
        logging.error("错误：TARGET_FOLDER_ID 未配置，请修改脚本。程序退出。")
        return

    _ = get_stoken_from_cookie(COOKIE) 

    while True:
        cycle_start_time = time.time()
        logging.info("--- 开始新一轮清理检查 ---")
        try:
            all_items_in_folder = list_all_items_in_folder(COOKIE, TARGET_FOLDER_ID)

            if not all_items_in_folder:
                logging.info("目标文件夹中未找到任何条目，或获取列表失败。")
            else:
                logging.info(f"在文件夹 [{TARGET_FOLDER_ID}] 中获取到 {len(all_items_in_folder)} 个条目。")

            items_to_delete_fids = []
            items_to_delete_details = [] # 用于日志记录 (文件名和类型)
            current_timestamp_seconds = int(time.time())

            for item_obj in all_items_in_folder:
                item_fid = item_obj.get('fid')
                item_name = item_obj.get('file_name')
                item_timestamp_ms = item_obj.get('itime_ms') 
                item_type = item_obj.get('type', '未知') # 获取类型

                if not (item_fid and item_name and item_timestamp_ms is not None):
                    logging.warning(f"跳过信息不完整的重要条目: {item_obj}")
                    continue
                
                try:
                    item_creation_timestamp_ms = int(item_timestamp_ms)
                    item_creation_timestamp_seconds = item_creation_timestamp_ms // 1000
                except ValueError:
                    logging.warning(f"条目 '{item_name}' (ID: {item_fid}) 的创建时间格式无效: '{item_timestamp_ms}'，已跳过。")
                    continue

                item_age_seconds = current_timestamp_seconds - item_creation_timestamp_seconds
                
                logging.debug( # 打印每个条目的时间判断详情
                    f"调试条目: '{item_name}' (ID: {item_fid}, 类型: {item_type})\n"
                    f"  - API返回时间戳 (ms): {item_timestamp_ms}\n"
                    f"  - 计算出的创建时间 (s): {item_creation_timestamp_seconds} (人类可读: {time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime(item_creation_timestamp_seconds))})\n"
                    f"  - 当前时间 (s): {current_timestamp_seconds} (人类可读: {time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime(current_timestamp_seconds))})\n"
                    f"  - 条目年龄 (s): {item_age_seconds} (约 {item_age_seconds / 3600:.2f} 小时)\n"
                    f"  - 是否符合删除条件 (年龄 > {DELETE_OLDER_THAN_SECONDS}s): {item_age_seconds > DELETE_OLDER_THAN_SECONDS}"
                )

                if item_age_seconds > DELETE_OLDER_THAN_SECONDS:
                    items_to_delete_fids.append(item_fid)
                    items_to_delete_details.append(f"{item_name} ({item_type})")
                    age_hours = item_age_seconds // 3600
                    age_minutes = (item_age_seconds % 3600) // 60
                    logging.info(f"条目 '{item_name}' ({item_type}, ID: {item_fid}) 符合删除条件 (已存在 {age_hours}小时 {age_minutes}分钟)。")

            if items_to_delete_fids:
                logging.info(f"准备删除以下条目: {', '.join(items_to_delete_details)}")
                delete_success = delete_quark_items(COOKIE, items_to_delete_fids) # 使用新的函数名
                if delete_success:
                    logging.info(f"成功发送删除 {len(items_to_delete_fids)} 个条目的请求。")
                else:
                    logging.error(f"删除 {len(items_to_delete_fids)} 个条目的请求失败。")
            else:
                logging.info("本轮检查未发现符合删除条件的条目。")

        except Exception as e:
            logging.error(f"主循环发生意外错误: {e}", exc_info=True)

        logging.info(f"--- 本轮清理检查结束 ---")
        # ... (sleep逻辑不变) ...
        cycle_duration = time.time() - cycle_start_time
        sleep_time = POLL_INTERVAL_SECONDS - cycle_duration
        if sleep_time > 0:
            logging.info(f"等待 {sleep_time:.2f} 秒后开始下一轮...")
            time.sleep(sleep_time)
        else:
            logging.info("本轮处理时间已超过轮询间隔，立即开始下一轮。")


# --- 4. 程序入口 ---
if __name__ == "__main__":
    main_loop()

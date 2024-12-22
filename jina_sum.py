# encoding:utf-8
import json
import os
import html
from urllib.parse import urlparse
import time

import requests

import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins import *

@plugins.register(
    name="JinaSum",
    desire_priority=10,
    hidden=False,
    desc="Sum url link content with jina reader and llm",
    version="0.0.1",
    author="hanfangyuan",
)
class JinaSum(Plugin):
    """网页内容总结插件
    
    功能：
    1. 自动总结分享的网页内容
    2. 支持手动触发总结
    3. 支持群聊和单聊不同处理方式
    4. 支持黑名单群组配置
    """
    # 默认配置
    DEFAULT_CONFIG = {
        "jina_reader_base": "https://r.jina.ai",
        "open_ai_api_base": "https://api.openai.com/v1",
        "open_ai_api_key": "",  # 添加 API key 配置项
        "open_ai_model": "gpt-3.5-turbo",
        "max_words": 8000,
        "prompt": "我需要对下面引号内文档进行总结，总结输出包括以下三个部分：\n📖 一句话总结\n🔑 关键要点,用数字序号列出3-5个文章的核心内容\n🏷 标签: #xx #xx\n请使用emoji让你的表达更生动\n\n",
        "white_url_list": [],
        "black_url_list": [
            "https://support.weixin.qq.com",  # 视频号视频
            "https://channels-aladin.wxqcloud.qq.com",  # 视频号音乐
        ],
        "black_group_list": [],
        "auto_sum": True,
        "cache_timeout": 60,  # 缓存超时时间（秒）
        "summary_cache_timeout": 300,  # 总结结果缓存时间（5分钟）
    }

    def __init__(self):
        super().__init__()
        try:
            self.config = super().load_config()
            if not self.config:
                self.config = self._load_config_template()
            
            # 使用默认配置初始化
            for key, default_value in self.DEFAULT_CONFIG.items():
                setattr(self, key, self.config.get(key, default_value))
            
            # 验证必要的配置
            if not self.open_ai_api_key:
                raise ValueError("OpenAI API key is required")
            
            # 初始化缓存
            self.pending_messages = {}  # 待处理消息缓存
            self.summary_cache = {}  # 总结结果缓存
            
            logger.info(f"[JinaSum] inited, config={self.config}")
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
        except Exception as e:
            logger.error(f"[JinaSum] 初始化异常：{e}")
            raise "[JinaSum] init failed, ignore "

    def on_handle_context(self, e_context: EventContext, retry_count: int = 0):
        try:
            context = e_context["context"]
            content = context.content
            msg = e_context['context']['msg']
            is_group = context.get("isgroup", True)
            
            # 生成消息的唯一标识
            chat_id = context.get("session_id", "default")
            
            # 清理过期的缓存
            self._clean_expired_cache()
            
            # 检查是否需要自动总结
            should_auto_sum = self.auto_sum
            if is_group and msg.from_user_nickname in self.black_group_list:
                # 黑名单群组强制关闭自动总结
                should_auto_sum = False
                logger.debug(f"[JinaSum] {msg.from_user_nickname} is in black group list, auto sum disabled")

            # 处理文本消息（用户触发总结）
            if context.type == ContextType.TEXT:
                content = content.strip()
                if is_group and content == "总结":
                    # 群聊中的总结触发
                    if chat_id in self.pending_messages:
                        cached_content = self.pending_messages[chat_id]["content"]
                        del self.pending_messages[chat_id]
                        return self._process_summary(cached_content, e_context, retry_count)
                elif content.startswith("总结 "):
                    # 处理"总结 URL"格式
                    url = content[3:].strip()
                    if chat_id in self.pending_messages:
                        del self.pending_messages[chat_id]
                    return self._process_summary(url, e_context, retry_count)
                return

            # 处理分享消息
            elif context.type == ContextType.SHARING:
                if is_group:
                    if should_auto_sum:
                        # 自动总结开启且不在黑名单中，直接处理
                        logger.debug(f"[JinaSum] Auto processing group message: {content}")
                        return self._process_summary(content, e_context, retry_count)
                    else:
                        # 自动总结关闭或在黑名单中，缓存消息等待触发
                        self.pending_messages[chat_id] = {
                            "content": content,
                            "timestamp": time.time()
                        }
                        logger.debug(f"[JinaSum] Cached group message: {content}")
                        return
                else:
                    # 单聊：直接处理
                    logger.debug(f"[JinaSum] Processing private chat message: {content}")
                    if chat_id in self.pending_messages:
                        del self.pending_messages[chat_id]
                    return self._process_summary(content, e_context, retry_count)
            
            return

        except Exception as e:
            logger.error(f"[JinaSum] Error: {str(e)}")
            if chat_id in self.pending_messages:
                del self.pending_messages[chat_id]
            if retry_count < 3:
                return self.on_handle_context(e_context, retry_count + 1)
            reply = Reply(ReplyType.ERROR, f"处理失败: {str(e)}")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

    def _clean_expired_cache(self):
        """清理过期的缓存"""
        current_time = time.time()
        # 清理待处理消息缓存
        expired_keys = [
            k for k, v in self.pending_messages.items() 
            if current_time - v["timestamp"] > self.cache_timeout
        ]
        for k in expired_keys:
            del self.pending_messages[k]
            
        # 清理总结结果缓存
        expired_keys = [
            k for k, v in self.summary_cache.items() 
            if current_time - v["timestamp"] > self.summary_cache_timeout
        ]
        for k in expired_keys:
            del self.summary_cache[k]

    def _process_summary(self, content: str, e_context: EventContext, retry_count: int = 0):
        """处理总结请求"""
        try:
            if not self._check_url(content):
                logger.debug(f"[JinaSum] {content} is not a valid url, skip")
                return
                
            # 检查缓存
            if content in self.summary_cache:
                cache_data = self.summary_cache[content]
                if time.time() - cache_data["timestamp"] <= self.summary_cache_timeout:
                    logger.debug(f"[JinaSum] Using cached summary for: {content}")
                    reply = Reply(ReplyType.TEXT, cache_data["summary"])
                    e_context["reply"] = reply
                    e_context.action = EventAction.BREAK_PASS
                    return
            
            if retry_count == 0:
                logger.debug("[JinaSum] Processing URL: %s" % content)
                reply = Reply(ReplyType.TEXT, "🎉正在为您生成总结，请稍候...")
                channel = e_context["channel"]
                channel.send(reply, e_context["context"])

            target_url = html.unescape(content)
            jina_url = self._get_jina_url(target_url)
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
            response = requests.get(jina_url, headers=headers, timeout=60)
            response.raise_for_status()
            target_url_content = response.text
            
            openai_chat_url = self._get_openai_chat_url()
            openai_headers = self._get_openai_headers()
            openai_payload = self._get_openai_payload(target_url_content)
            
            response = requests.post(openai_chat_url, headers=openai_headers, json=openai_payload, timeout=60)
            response.raise_for_status()
            result = response.json()['choices'][0]['message']['content']
            
            # 缓存总结结果
            self.summary_cache[content] = {
                "summary": result,
                "timestamp": time.time()
            }
            
            reply = Reply(ReplyType.TEXT, result)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            
        except Exception as e:
            logger.error(f"[JinaSum] Error in processing summary: {str(e)}")
            if retry_count < 3:
                return self._process_summary(content, e_context, retry_count + 1)
            reply = Reply(ReplyType.ERROR, f"无法获取或总结该内容: {str(e)}")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

    def get_help_text(self, verbose, **kwargs):
        help_text = "网页内容总结插件:\n"
        help_text += "1. 发送「总结 网址」可以总结指定网页的内容\n"
        help_text += "2. 单聊时分享消息会自动总结\n"
        if self.auto_sum:
            help_text += "3. 群聊中分享消息默认自动总结"
            if self.black_group_list:
                help_text += "（部分群组需要发送「总结」触发）\n"
            else:
                help_text += "\n"
        else:
            help_text += "3. 群聊中收到分享消息后，发送「总结」即可触发总结\n"
            help_text += "注：群聊中的分享消息的总结请求需要在60秒内发出"
        return help_text

    def _load_config_template(self):
        logger.debug("No Suno plugin config.json, use plugins/jina_sum/config.json.template")
        try:
            plugin_config_path = os.path.join(self.path, "config.json.template")
            if os.path.exists(plugin_config_path):
                with open(plugin_config_path, "r", encoding="utf-8") as f:
                    plugin_conf = json.load(f)
                    return plugin_conf
        except Exception as e:
            logger.exception(e)

    def _get_jina_url(self, target_url):
        return self.jina_reader_base + "/" + target_url

    def _get_openai_chat_url(self):
        return self.open_ai_api_base + "/chat/completions"

    def _get_openai_headers(self):
        return {
            'Authorization': f"Bearer {self.open_ai_api_key}",
            'Host': urlparse(self.open_ai_api_base).netloc
        }

    def _get_openai_payload(self, target_url_content):
        target_url_content = target_url_content[:self.max_words] # 通过字符串长度简单行截
        sum_prompt = f"{self.prompt}\n\n'''{target_url_content}'''"
        messages = [{"role": "user", "content": sum_prompt}]
        payload = {
            'model': self.open_ai_model,
            'messages': messages
        }
        return payload

    def _check_url(self, target_url: str):
        """检查URL是否有效且允许访问
        
        Args:
            target_url: 要检查的URL
            
        Returns:
            bool: URL是否有效且允许访问
        """
        stripped_url = target_url.strip()
        # 简单校验是否是url
        if not stripped_url.startswith("http://") and not stripped_url.startswith("https://"):
            return False

        # 检查白名单
        if len(self.white_url_list):
            if not any(stripped_url.startswith(white_url) for white_url in self.white_url_list):
                return False

        # 排除黑名单，黑名单优先级>白名单
        for black_url in self.black_url_list:
            if stripped_url.startswith(black_url):
                return False

        return True

# jina_sumary
ChatGPT on WeChat项目插件, 使用jina reader和ChatGPT总结网页链接内容

微信公众号链接近期更新了，公众号卡片链接会进行校验，暂时没有找到合适的方法从公众号卡片链接获取到直接链接，但是此插件能总结公众号直接链接，和其他卡片链接(如小红书，哔哩哔哩等)

config.json 配置说明
```json
{
  "jina_reader_base": "https://r.jina.ai",           // jina reader链接，默认为https://r.jina.ai
  "open_ai_api_base": "https://api.openai.com/v1",   // chatgpt chat url
  "open_ai_api_key":  "sk-xxx",                      // chatgpt api key
  "open_ai_model": "gpt-3.5-turbo",                  // chatgpt model
  "max_words": 8000,                                 // 网页链接内容的最大字数，防止超过最大输入token，使用字符串长度简单计数
  "prompt": "我需要对下面的文本进行总结，总结输出包括以下三个部分：\n📖 一句话总结\n🔑 关键要点,用数字序号列出3-5个文章的核心内容\n🏷 标签: #xx #xx\n请使用emoji让你的表达更生动。"                           // 链接内容总结提示词
}
```
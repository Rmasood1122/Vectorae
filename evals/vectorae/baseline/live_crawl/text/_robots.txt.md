# Policy: all search and AI crawlers may read all public pages.
# Decision record: docs/program/ai-visibility/geo/technical/crawler_access_strategy.md
# A crawler that matches a named group ignores the * group (RFC 9309), so every
# group repeats Allow: /.

# Search engines (Google Search incl. AI Overviews and AI Mode; Bing incl. Copilot)
User-agent: Googlebot
Allow: /

User-agent: Bingbot
Allow: /

# Google: Gemini training and Gemini Apps grounding (token only; no effect on Search)
User-agent: Google-Extended
Allow: /

# OpenAI: ChatGPT search, user-requested fetches, training
User-agent: OAI-SearchBot
Allow: /

User-agent: ChatGPT-User
Allow: /

User-agent: GPTBot
Allow: /

# Anthropic: Claude search index, user-requested fetches, training (anthropic-ai is legacy)
User-agent: Claude-SearchBot
Allow: /

User-agent: Claude-User
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: anthropic-ai
Allow: /

# Perplexity
User-agent: PerplexityBot
Allow: /

User-agent: Perplexity-User
Allow: /

# Apple, Meta, Amazon, DuckDuckGo, Mistral, Cohere, Common Crawl
User-agent: Applebot
Allow: /

User-agent: Applebot-Extended
Allow: /

User-agent: Meta-ExternalAgent
Allow: /

User-agent: Meta-WebIndexer
Allow: /

User-agent: Amazonbot
Allow: /

User-agent: DuckAssistBot
Allow: /

User-agent: MistralAI-User
Allow: /

User-agent: cohere-ai
Allow: /

User-agent: CCBot
Allow: /

# Everyone else
User-agent: *
Allow: /

Sitemap: https://www.calendarharvest.com/sitemap.xml

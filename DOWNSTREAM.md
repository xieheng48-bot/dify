# Downstream additions

This fork contains additions maintained by [谢恒 (xieheng48-bot)](https://github.com/xieheng48-bot). They have not been merged into the upstream Dify repository and are not endorsed by LangGenius or the upstream contributors.

| Addition | Scope |
| --- | --- |
| [Commerce assistant](extensions/commerce-assistant/README.md) | Standalone FastAPI companion service with demo order authorization, versioned policy filtering, explicit human confirmation, persistent idempotent return tickets, and optional Dify Chatflow integration |
| [Code walkthrough](extensions/commerce-assistant/CODE_WALKTHROUGH.md) | Chinese explanation of the implementation and exercises |
| [Authorship and provenance](extensions/commerce-assistant/AUTHORS.md) | Downstream maintainer responsibilities and transparent AI assistance |

The extension's default mode uses deterministic rules and lexical policy filtering. It does not implement vector RAG, access a real commerce platform, issue refunds, modify Dify core, or constitute a Dify plugin. Real Dify deployment and end-to-end model behavior have not been verified as part of the initial local tests.

All upstream Dify code, authorship, notices, and [license terms](LICENSE) remain intact. The maintainer's attribution applies to the additions identified above and does not claim authorship of Dify's existing platform capabilities.

# CODEBASE

Generated: 2026-03-14T20:56:59.638540+00:00

## Architecture Overview
This node repository maps to 1832 modules, 11 datasets, and 8 transformations. This is a VS Code extension called RooCode that provides AI-assisted coding through an integrated chat interface. It orchestrates AI assistant tasks, integrates with multiple AI providers (OpenAI, AWS Bedrock, Codex), manages webview UI components, handles tool execution, and supports Model Context Protocol (MCP) servers for extended functionality.

## Critical Path
- `src/shared/api.ts` (0.47): This module provides shared TypeScript utilities and type definitions for configuring and routing API requests to AI models, including provider-specific settings, reasoning capabilities, and token management.
- `src/i18n/index.ts` (0.44): This module provides internationalization (i18n) functionality by exposing methods to initialize, change, and retrieve the current language, as well as translate strings using i18next.
- `src/core/webview/ClineProvider.ts` (0.40): This module provides core task management and webview integration functionality for the RooCode extension, handling task lifecycle events, provider communication, and UI state synchronization. It serves as the central hub for coordinating AI-assisted coding tasks within the VS Code environment.
- `src/core/task/Task.ts` (0.39): This module implements the core task execution engine that manages AI assistant conversations, handles API interactions with multiple providers, tracks token and tool usage, and coordinates message flow including streaming responses and user interactions.
- `src/utils/fs.ts` (0.33): This utility module provides filesystem operations for creating directory structures and checking file existence asynchronously. It supports cross-platform path handling and tracks newly created directories for potential cleanup.

## Data Sources And Sinks
Sources: `public.runs`, `public.taskMetrics`, `public.tasks`, `runs`, `tasks_language_exercise_idx`
Sinks: None detected.

## Known Debt
Circular dependency clusters: 10. Blind spots: 123. Semantic review queue items: 50.

## High-Velocity Files
- `apps/cli/eslint.config.mjs` (1 commits in the configured window)
- `apps/cli/install.sh` (1 commits in the configured window)
- `apps/cli/scripts/build.sh` (1 commits in the configured window)
- `apps/cli/scripts/test-stdin-stream.ts` (1 commits in the configured window)
- `apps/cli/src/__tests__/index.test.ts` (1 commits in the configured window)

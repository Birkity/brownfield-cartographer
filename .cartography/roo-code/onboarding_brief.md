# Onboarding Brief

Generated: 2026-03-14T20:56:59.640726+00:00

## What does this codebase do at a high level?
This is a VS Code extension called RooCode that provides AI-assisted coding through an integrated chat interface. It orchestrates AI assistant tasks, integrates with multiple AI providers (OpenAI, AWS Bedrock, Codex), manages webview UI components, handles tool execution, and supports Model Context Protocol (MCP) servers for extended functionality.

Supporting citations:
- `src/core/task/Task.ts`:83-4659 [phase3/semantic] Module description states it implements core task execution engine for AI assistant conversations, handles API interactions with multiple providers, and coordinates message flow
- `src/core/webview/ClineProvider.ts`:1-3597 [phase3/semantic] Module description indicates it provides core task management and webview integration for RooCode extension, handling AI-assisted coding tasks within VS Code
- `webview-ui/src/components/chat/ChatView.tsx`:67-1851 [phase3/semantic] Component description confirms it's the main chat interface for AI-assisted coding conversations in a VS Code extension

## What are the main data flows and where does data come from?
Data flows primarily through: 1) User input from webview UI components, 2) AI provider API integrations (OpenAI, AWS Bedrock, Codex), 3) Tool execution results from native tools and MCP servers, and 4) Configuration settings. The webview message handler serves as the central routing hub between UI and backend services.

Supporting citations:
- `src/core/webview/webviewMessageHandler.ts`:1-3510 [phase3/semantic] Module handles bidirectional communication between webview UI and backend, processing user interactions and integrating with terminal, git, file system, and cloud features
- `src/api/providers/openai-native.ts`:35-1472 [phase3/semantic] Module implements OpenAI API provider handler that manages AI model interactions and streaming responses
- `src/shared/api.ts`:11-26 [phase3/semantic] Module provides shared utilities for configuring and routing API requests to AI models with provider-specific settings

## What are the critical modules that a new engineer should understand first?
The most critical modules are: 1) Task.ts (core task execution engine), 2) ClineProvider.ts (task management and webview integration), 3) webviewMessageHandler.ts (central message routing), 4) ChatView.tsx (main UI component), and 5) the API provider modules (openai-native.ts, bedrock.ts) for AI integrations.

Supporting citations:
- `src/core/task/Task.ts`:83-4659 [phase3/semantic] Top module by PageRank - implements core task execution engine that manages AI assistant conversations and coordinates message flow
- `src/core/webview/ClineProvider.ts`:1-3597 [phase3/semantic] Second top module by PageRank - serves as central hub for coordinating AI-assisted coding tasks
- `src/core/webview/webviewMessageHandler.ts`:1-3510 [phase3/semantic] Fourth top module by PageRank - serves as central message routing and processing hub for application's core functionality

## Where are the highest-risk areas and technical debt?
Highest risks include: 1) 11 circular dependencies indicating potential architectural issues, 2) 1108 dead-code candidates suggesting significant code bloat or poor maintenance, 3) Complex integration points across multiple AI providers and MCP servers, and 4) Bidirectional communication between webview and backend which can be error-prone.

Supporting citations:
- `EVIDENCE SUMMARY` [phase1/hotspot] Evidence summary shows 11 circular dependencies and 1108 dead-code candidates across the codebase
- `src/core/webview/webviewMessageHandler.ts`:1-3510 [phase3/semantic] Central message routing hub handling multiple integration points increases complexity and failure risk

## What are the blind spots — areas where the analysis may be incomplete?
Major blind spots include: 1) No datasets or transformations identified despite lineage summary showing 11 table_ref datasets and 8 transformations, 2) 1108 dead-code candidates that haven't been validated, 3) No architectural hubs identified despite complex module interactions, and 4) Limited understanding of the evaluation system and cloud services integration.

Supporting citations:
- `EVIDENCE SUMMARY` [phase1/drift] Evidence shows 0 datasets/transformations but lineage summary shows 11 datasets and 8 transformations - indicates potential analysis gaps
- `EVIDENCE SUMMARY` [phase1/hotspot] 1108 dead-code candidates identified but not validated - represents significant uncertainty in codebase understanding
- `apps/web-evals/src/app/runs/new/new-run.tsx`:32-34 [phase3/semantic] Evaluation system module identified but limited details on how it integrates with main application


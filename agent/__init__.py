"""Tool-augmented agentic equipment isolation (Gemini orchestrator).

The LLM is the orchestrator: it runs in a tool-calling loop and decides which
deterministic pipeline stage to call next. The existing deterministic modules
(boundary, candidates, bbox, evidence, planner, validator, output) are preserved
unchanged and exposed to the agent as tools. The deterministic ``validate()``
remains the AUTHORITATIVE source of the safety verdict; the agent may gather
more evidence but cannot declare isolation on its own.
"""

from anthropic import Anthropic

DEFAULT_MODEL = "claude-sonnet-5"

def run_agent(
    messages,
    tools,
    tool_functions,
    system=None,
    model=DEFAULT_MODEL,
    max_tokens=1024,
    max_iterations=10,
):
    """Run the Anthropic tool-use loop until Claude gives a final text answer.

    `messages` is mutated in place with the full conversation history
    (assistant tool-use turns and the corresponding tool_result turns),
    following the same convention as `tools.py`'s add_*_message helpers.

    `tool_functions` maps a tool name to a callable(input_dict) -> str. If a
    tool call raises, or names a tool not present in `tool_functions`, that
    is reported back to Claude as the tool_result content instead of raising
    out of the loop.

    Raises RuntimeError if `max_iterations` is exceeded without Claude
    producing a final text-only response.
    """
    anthropicClient = Anthropic()

    for _ in range(max_iterations):
        response = anthropicClient.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_use_blocks = [block for block in response.content if block.type == "tool_use"]
        if not tool_use_blocks:
            text_blocks = [block.text for block in response.content if block.type == "text"]
            return "\n".join(text_blocks)

        tool_results = []
        for block in tool_use_blocks:
            tool_function = tool_functions.get(block.name)
            if tool_function is None:
                content = f"Error: no implementation registered for tool '{block.name}'"
            else:
                try:
                    content = str(tool_function(block.input))
                except Exception as exc:
                    content = f"Error running tool '{block.name}': {exc}"
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": content}
            )

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Agent exceeded max_iterations ({max_iterations}) without a final answer")

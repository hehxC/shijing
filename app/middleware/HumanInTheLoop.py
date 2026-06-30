from langchain.agents.middleware import HumanInTheLoopMiddleware

human_in_loop_middleware = HumanInTheLoopMiddleware(
    interrupt_on={
        "is_satisfied": {
            "description": "是否满意",
            "allowed_decisions": ["approve", "reject", "edit"]
        }
    }
)

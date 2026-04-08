from typing import TypedDict
from langgraph.graph import StateGraph, START, END

# State
class BotState(TypedDict):
    messages: list[dict] 
    avatar: str | None
    onboarding_shown: bool
    ferpa_blocked : bool
    response: str | None
    rag_chunks: list[dict]
    intent: str | None


def onboarding(state:BotState):
    pass

def ferpa_sanitizer(state:BotState):
    pass

def intent_router(state:BotState):
    pass

def rag_node(state:BotState):
    pass

def persona_adapter(state:BotState):
    pass

def boundary_node(state:BotState):
    pass

def route_after_ferpa(state:BotState):
    pass

def route_intent(state:BotState):
    pass

#Graph
graph = StateGraph(BotState)

graph.add_node('onboarding', onboarding)
graph.add_node('ferpa_sanitizer', ferpa_sanitizer)
graph.add_node('intent_router', intent_router)
graph.add_node('rag_node', rag_node)
graph.add_node('persona_adapter', persona_adapter)
graph.add_node('boundary_node', boundary_node)

graph.add_edge(START, 'onboarding')
graph.add_edge('onboarding','ferpa_sanitizer')

graph.add_conditional_edges('ferpa_sanitizer', route_after_ferpa, {
    "blocked":      END,
    "route_intent": "intent_router",
})

graph.add_conditional_edges("intent_router", route_intent, {
    "concept":   "rag_node",
    "boundary":  "boundary_node",
})

graph.add_edge("rag_node",        "persona_adapter")
graph.add_edge("persona_adapter", END)
graph.add_edge("boundary_node",   END)

compiled = graph.compile()
compiled.get_graph().print_ascii()
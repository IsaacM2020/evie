def make_answers(for_evie=0.9, route="quick_action", conf=1.0, complete=0.9, has_event=0.0):
    return {
        "for_evie": {"type": "noul", "noul": for_evie},
        "route": {"type": "choice", "choice": route, "probabilities": {route: conf}, "confidence": conf},
        "complete": {"type": "noul", "noul": complete},
        "has_event": {"type": "noul", "noul": has_event},
    }

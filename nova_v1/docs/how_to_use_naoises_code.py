"""
How to use Naoise's gain code :)
"""

from app.tools.registry import ToolRegistry
from app.tools.dispatcher import Dispatcher
from app.gain.gain_store import GainStore
from app.gain.reinforcement import Reinforcer, Outcome

# ============================
# set-up (georgia... i think?)
# ============================

gain_store = GainStore()
registry = ToolRegistry(gain_store=gain_store)
dispatcher = Dispatcher(registry)

# add tools to the register (fill in brackets once tools are complete)
registry.register(<ToolInstance>)   # Function 1's tool
registry.register(<ToolInstance>)   # Function 2's tool
registry.register(<ToolInstance>)   # Function 3's tool

schemas = registry.get_schemas() 
# schemas is type list[ToolSchema], includes each tool's current gain
# will be passed to the model



# =====================================
# reactive Function tool call (georgia)
# =====================================

result = dispatcher.dispatch_reactive("calendar_add_event", {"title": "Lecture", "time": "3pm"})
# where "calendar_add_event" is the name of the Function tool
# and the second argument is the inputs in the form of a dictionary
# need to wait for ella


# ======================================
# proactive Function tool call (georgia)
# ======================================

reinforcer = Reinforcer(registry, gain_store)

proposal = dispatcher.dispatch_proactive(
    "calendar_add_event",
    {"title": "Lecture", "time": "3pm"},
    state_confidence=0.8,   # comes from state estimator
)

if proposal.proposed:
    # confidence * gain cleared >= threshold then ask the user
    # e.g. speak "Want me to add lecture to calendar?" and wait for yes/no
    if user_said_yes:
        dispatcher.confirm_proactive(
            "calendar_add_event", 
            {"title": "Lecture", "time": "3pm"}
            )

        # user accepted the proactive reminder -> gain nudges up
        reinforcer.reinforce("calendar_add_event", Outcome.ACCEPTED)
    else:
        # user rejected it -> gain nudges down
        reinforcer.reinforce("calendar_add_event", Outcome.REJECTED)
else:
    # not confident/proactive enough yet, do nothing
    pass
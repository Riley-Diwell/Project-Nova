"""
Checks whether a tool is allowed to run 
proactively. A tool can only fire when:

    state_confidence x gain >= FIRING_THRESHOLD

NOTE: Georgia this runs after the intent surface.
Intent surface figures out what tools to call 
given an event and user state. This just makes
sure they arn't run unless it's confident and 
the tool is allowed to run by proactively. 
Thoughts? Would you agree with that?
"""
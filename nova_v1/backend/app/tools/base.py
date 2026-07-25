"""
Base class for all Nova.

Every tool gets:
- a name
- a description
- an input format
- a gain value
- an invoke() function to run it

A new tool only needs to write _execute().
"""

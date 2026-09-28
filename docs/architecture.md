# Architecture

Deterministic geometry core (FreeCAD + Python) does all CAD work.
A local LLM is consulted only on failure, and only for diagnosis
+ selection from a fixed allow-list of recovery actions.

See README.md for the diagram and the "what the LLM does / does not do" table.
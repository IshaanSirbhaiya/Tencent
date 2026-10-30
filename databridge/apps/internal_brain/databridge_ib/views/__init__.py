"""Just-in-time views of an answer (``POST /ib/views``): the person says how they want an answer
laid out ("a table, then the timeline and the Slack thread") and gets a one-page view built from
the sources the answer cited, sent as A2UI v0.9 messages for the web to render.

An intelligence layer on top of the Brain: it reads only the asker's own released answer and the
cited documents they can still open (the Act context), every value passes the deliverables checks,
and it is ledgered like a deliverable (``deliverable="view"``). Same kill switch as deliverables.
"""

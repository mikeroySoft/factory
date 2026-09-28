“Build an AI assistant so developers can get their first GPU workload running.”

Imagine this request lands in your queue. An agent can start building. But what should it build?

We have named a solution: an assistant. We have not yet established where developers get stuck—or whether an assistant would help.

The PM’s first job is to close that gap.

Let’s make this concrete with rocm-cli, where we are the main architect and contributor. Before we decide what to add to it, watch a developer try to get their first GPU workload running.

Where do they stop? What did they expect? What do they try next?

Knowing how we built the tool is not the same as knowing where its users get stuck. We are not yet choosing a feature. We are finding out what prevents the developer from succeeding.

Suppose we see a developer stop at an error message. They cannot tell what failed or what to do next.

An assistant might help. But a clearer error message might be enough.

For rocm-cli, the question becomes smaller: what would help this developer take the next correct step?

We started with a request for an assistant. We now have a specific question about an error message.

That is progress, even though we have not shipped anything.

The PM has helped the team replace a broad request with a problem it can investigate. Faster coding makes this work more important, not less.

When building takes a long time, much of a PM’s attention goes to getting work ready and keeping it moving.

Agents can shorten parts of that work. But they do not make a request worth doing simply by making it easier to implement.

The PM must put more attention into choosing the work—not just moving it through the queue.

Now consider a queue full of requests like “build an assistant.”

Some describe a problem we understand. Others name a solution before anyone has checked the problem.

These requests do not all need the same next step. Some are ready to build. Some need a question answered first. Some should wait.

“We need more information” is not a useful stopping point.

For the rocm-cli example, name the question: can the developer understand the error and choose the correct next action?

Assign someone to check it with a developer, then agree when to review what they found. The request now has a next step without becoming a commitment to build an assistant.

Before we run the check, agree what we will do with the answer.

If the developer understands the message but cannot complete the next step, clearer wording may not be enough. If they can proceed, the message change is worth testing further.

The investigation should help us choose—not just produce another report.

Once we know what question we need to answer, we can give an agent useful work.

For rocm-cli, it could trace the error, draft alternative messages, or implement a candidate change for review.

The team still checks whether the explanation is correct and whether it helps the developer. The agent speeds up the work; it does not replace that judgment.

An agent may prepare changes faster than the team can review them.

Starting more work does not help if completed changes are waiting for someone to check them.

The PM must help the team see what is holding up delivery—not simply ask the agents to produce more.

Before starting another change, ask what the team can review, test, and release.

If that capacity is already full, choose what should finish first. Keep other requests visible, but do not start them merely because an agent is available.

The aim is to get useful changes into developers’ hands—not to keep every agent busy.

Suppose we release the clearer rocm-cli message.

The change is complete, but our question remains: can developers now understand the failure and take the correct next step?

Check what happens when they use it. Shipping tells us that we delivered software. Their experience tells us whether we solved the problem.

Take one unclear request from your own queue.

Who needs help, and what are they trying to do? What do you still need to learn? What would show that the work helped?

Choose the next step: build, investigate, or wait. Name who will take that step and when you will review the result.

Compare your next step with the original request.

Are you still planning the same work? If not, what changed your mind?

If you chose to investigate, explain what decision the answer will help you make. If you chose to build, explain what already gives you confidence. If you chose to wait, explain what would let the work move forward.

The PM’s job is not to turn every request into a feature.

It is to help the team understand the problem, choose useful work, and check whether that work helped.

Agents can make implementation faster. The PM helps the team decide where that speed is worth using.

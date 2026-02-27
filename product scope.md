

Inspiration
We’ve all heard the labels: lazy, unmotivated, or failing to live up to potential. But for 1 in 16 adults, the problem isn't a lack of discipline; it's a difference in brain chemistry. This goes beyond simple procrastination or poor focus; it is rooted in Executive Dysfunction. It's not like we don't know better, but bridging the gap between knowing what to do and actually doing it is often where we fail and existing productivity apps promise fixes but they require executive function to use them in the first place, which is the very thing impaired by ADHD. This is why current productivity tools will not work.

Inspired by Dr. Russell Barkley, the father of ADHD research, his concept of creating a prosthetic environment, an external system to compensate for internal deficits, which is why we built a proactive agent to borrow the executive burden of your life so you can focus on your goals and not struggle to even do the basic everyday tasks to survive.

What it does
The agent augments the brain’s 'management system' responsible for organizational control, emotional stability, and sustained focus. Biologically, these are the native responsibilities of the prefrontal cortex; when that brain region is inconsistent, this agent acts as an external scaffold to provide structure that is otherwise absent.

Autonomous Agency: The agent operates on its own timeline. It "sleeps" and "wakes" whenever it wants, possessing the agency to decide when an intervention is necessary, it reaches out when it senses a gap between the user’s goals and their current state.

Total Context Awareness: By having access to Google Calendar and Tasks, the agent understands the difference between a minor delay and a total derailment, allowing it to provide a persistent external scaffold that stays upright even when the user’s focus collapses.

Proactive Scaffolding: Using clinical techniques like Cognitive Behavioral Therapy and others, the agent interrupts "Executive Dysfunction" by initiating voice-first sessions. It doesn't just remind; it recalibrates the user's mental state, shifting them from a state of overwhelm to a state of agency.

Generative UI: Instead of static lists, the agent uses Gemini function calling to manifest a Live Workspace. It renders only what is necessary for the current "state of mind," physically narrowing the user's world down to the next achievable win.

How we built it
The agent has two modes:

First the Conversation Mode using Gemini Live API to handle the Mobile App connection over WebSocket for low-latency speech to speech to talk to users.
Second is the Background Mode, which runs independently using Gemini 3 Pro on Standard API, using cron scheduler tool like a timer and access to Google Calendar and Tasks via Composio to decide when to proactively intervene.
Both modes share a common session through the Agent Development Kit which ensures that when a user moves from a background mode to a live conversation, the session context is preserved, creating one seamless chat thread throughout the day.
Challenges we ran into
The core challenge was transforming a reactive LLM into a proactive agent with a sense of time. Most AI assistants wait for a prompt; Intentive needed the agency to "wake up" and reach out. We had to engineer a 24-hour temporal context that allows the agent to understand where the user is in their day without being prompted.

Additionally, we faced a major hurdle with Session Persistence across modes. Maintaining a single, cohesive "state of mind" while switching between background monitoring and the Gemini Live foreground session was difficult. We had to implement complex context engineering to ensure that when the agent reaches out to the user, it carries the full weight of their previous interactions and current goals into the live conversation.

Accomplishments that we're proud of
Making it entirely proactive meaning the user isn't coming to the app, so it's always the agent that's reaching out to them and making sure they are aligned towards their goals.

By successfully giving the agent agency and a sense of time, we moved away from the traditional "user-to-app" interaction model. Achieving this proactive "reach out" capability while maintaining human-like latency via the Gemini Live API proves that AI can act as a true external scaffold for the human brain.

Built With
composio
cron-jobs.org
expo-notifications
expo-realtime-audio
expo-router
fastapi
firebase
firestore
gemini-live-api
google-adk
google-calendar-api
google-genai
google-tasks-api
python
react-native
render
testflight
typescript
websockets

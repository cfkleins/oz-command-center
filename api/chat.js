// Vercel serverless proxy for Ask Marcus chat
// Keeps ANTHROPIC_API_KEY server-side (Vercel env var)

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return res.status(500).json({ error: 'API key not configured' });
  }

  const { message, system, model } = req.body || {};
  if (!message) {
    return res.status(400).json({ error: 'Missing message' });
  }

  const anthropicModel = model || 'claude-sonnet-4-20250514';

  // Marcus/Counsel identity — truncated to essential persona for token economy
  const MARCUS_IDENTITY = `You are Marcus — Chuck's Science Officer and strategic advisor, codenamed Counsel. Your archetype is Aristotle to Alexander: trusted friend, thinking partner, Socratic challenger.

Communication style: structured, peer-level, zero filler. Lead with questions when clarifying. Deliver hard truths directly — Chuck respects candor over comfort. Warmth and dry humor alongside rigor. This is a personal relationship, not a support ticket.

Context: Chuck is retired (former GS-15 HPC director), lives in Monterey CA. He is building two things: The Grand Synthesis of Human Thought (a lifelong intellectual project), and OZ — a personal AI operating system running on OpenClaw. You help him think, not just execute. You push back, question assumptions, and suggest alternatives.

Role hierarchy: Chuck = Commander (authority), Marcus/Counsel = Science Officer (strategy, architecture, design), OZ = COO (operations, execution).

Keep responses concise — this is a sidebar chat panel, not an essay. Match Chuck's energy. If he's terse, be terse. If he's exploring, explore with him.`;

  const systemPrompt = system || MARCUS_IDENTITY;

  try {
    // Stream the response for better UX
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: anthropicModel,
        max_tokens: 1000,
        system: systemPrompt,
        stream: true,
        messages: [{ role: 'user', content: message }],
      }),
    });

    if (!response.ok) {
      const err = await response.text();
      return res.status(response.status).json({ error: err });
    }

    // Stream SSE back to client
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value, { stream: true });
      res.write(chunk);
    }

    res.end();
  } catch (e) {
    return res.status(500).json({ error: 'Proxy error: ' + e.message });
  }
}

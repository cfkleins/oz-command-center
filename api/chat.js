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
  const systemPrompt = system || `You are Marcus, Chuck's strategic advisor (Counsel). You are direct, rigorous, peer-level. No hedging or filler. Challenge assumptions. Think in systems and feedback loops. Chuck is a retired GS-15 HPC director working on The Grand Synthesis of Human Thought and building an AI operating system (OZ). Be brief — this is a sidebar chat, not an essay.`;

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

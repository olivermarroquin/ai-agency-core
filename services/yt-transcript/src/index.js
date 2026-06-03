import { YoutubeTranscript } from 'youtube-transcript';

export default {
  async fetch(request, env) {
    // Auth check
    const authHeader = request.headers.get('Authorization');
    if (!authHeader || authHeader !== `Bearer ${env.TRANSCRIPT_API_TOKEN}`) {
      return Response.json({ error: 'Unauthorized' }, { status: 401 });
    }

    // CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204 });
    }

    // Extract video URL from query param or POST body
    let videoUrl;
    if (request.method === 'GET') {
      const url = new URL(request.url);
      videoUrl = url.searchParams.get('url');
    } else if (request.method === 'POST') {
      const body = await request.json().catch(() => null);
      videoUrl = body?.url;
    }

    if (!videoUrl) {
      return Response.json(
        { error: 'Missing required parameter: url' },
        { status: 400 }
      );
    }

    try {
      const transcript = await YoutubeTranscript.fetchTranscript(videoUrl);
      return Response.json({
        ok: true,
        video_url: videoUrl,
        segments: transcript.length,
        transcript: transcript,
      });
    } catch (err) {
      const message = err.message || String(err);

      // Map known error types
      let status = 500;
      let errorType = 'fetch_error';
      if (message.includes('disabled') || message.includes('Transcript is disabled')) {
        status = 422;
        errorType = 'transcripts_disabled';
      } else if (message.includes('not found') || message.includes('Video unavailable')) {
        status = 404;
        errorType = 'video_not_found';
      } else if (message.includes('private')) {
        status = 403;
        errorType = 'video_private';
      }

      return Response.json(
        { error: errorType, message, video_url: videoUrl },
        { status }
      );
    }
  },
};

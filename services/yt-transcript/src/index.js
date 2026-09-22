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

      // Map known error types.
      // Error strings verified against youtube-transcript npm lib source (dist/esm/index.js):
      //   YoutubeTranscriptDisabledError    → "Transcript is disabled on this video"
      //   YoutubeTranscriptNotAvailableError → "No transcripts are available for this video"
      //   YoutubeTranscriptVideoUnavailableError → "The video is no longer available"
      //   YoutubeTranscriptTooManyRequestError   → "YouTube is receiving too many requests…captcha"
      // Note: age-restricted content is not detectable via message sniffing — the InnerTube
      // Android client path may bypass some age gates silently; when it does fail, it surfaces
      // as fetch_error (undetectable without additional response context from the library).
      let status = 500;
      let errorType = 'fetch_error';
      if (message.includes('Transcript is disabled') || message.includes('disabled')) {
        status = 422;
        errorType = 'transcripts_disabled';
      } else if (message.includes('No transcripts are available')) {
        // YoutubeTranscriptNotAvailableError — no captions at all on this video
        status = 422;
        errorType = 'transcripts_not_available';
      } else if (message.includes('no longer available') || message.includes('not found') || message.includes('Video unavailable')) {
        // YoutubeTranscriptVideoUnavailableError throws "The video is no longer available"
        status = 404;
        errorType = 'video_not_found';
      } else if (message.includes('private')) {
        // Legacy check — no current library error contains "private";
        // kept for forward-compatibility with future library versions.
        status = 403;
        errorType = 'video_private';
      } else if (message.includes('too many requests') || message.includes('captcha')) {
        // YoutubeTranscriptTooManyRequestError — rate-limited / captcha challenge
        status = 429;
        errorType = 'rate_limited';
      }

      return Response.json(
        { error: errorType, message, video_url: videoUrl },
        { status }
      );
    }
  },
};

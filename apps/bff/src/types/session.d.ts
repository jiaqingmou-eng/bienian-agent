import 'express-session';
import 'pino-http';

declare module 'express-session' {
  interface SessionData {
    actorId?: string;
  }
}

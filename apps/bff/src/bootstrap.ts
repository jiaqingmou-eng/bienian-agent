import { INestApplication, ValidationPipe } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import session from 'express-session';
import helmet from 'helmet';
import { HttpErrorFilter } from './common/http-error.filter';

/** Share the actual middleware and validation setup between runtime and HTTP tests. */
export function configureApp(app: INestApplication): void {
  const config = app.get(ConfigService);
  app.use(helmet());
  app.use(session({
    name: 'enterprise.sid',
    secret: config.getOrThrow<string>('SESSION_SECRET'),
    resave: false,
    saveUninitialized: false,
    cookie: { httpOnly: true, sameSite: 'strict', secure: false, maxAge: 8 * 60 * 60 * 1000 },
  }));
  app.useGlobalPipes(new ValidationPipe({
    transform: true,
    whitelist: true,
    forbidNonWhitelisted: true,
    forbidUnknownValues: true,
    validationError: { target: false, value: false },
  }));
  app.useGlobalFilters(new HttpErrorFilter());
  app.use((_req: unknown, res: { setHeader: (key: string, value: string) => void }, next: () => void) => {
    res.setHeader('Cache-Control', 'no-store');
    next();
  });
  app.enableShutdownHooks();
}

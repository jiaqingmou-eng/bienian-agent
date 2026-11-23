import 'reflect-metadata';
import { NestFactory } from '@nestjs/core';
import { ConfigService } from '@nestjs/config';
import { Logger } from 'nestjs-pino';
import { AppModule } from './app.module';
import { configureApp } from './bootstrap';

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule, { bufferLogs: true });
  app.useLogger(app.get(Logger));
  configureApp(app);
  const config = app.get(ConfigService);
  await app.listen(config.getOrThrow<number>('BFF_PORT'), config.get<string>('BFF_HOST') ?? '127.0.0.1');
}

void bootstrap().catch(() => {
  // Configuration errors can contain secrets; keep the startup error deliberately generic.
  console.error('BFF startup failed. Check required environment variables and port availability.');
  process.exitCode = 1;
});

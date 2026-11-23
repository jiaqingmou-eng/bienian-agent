import { Body, Controller, Get, HttpCode, Post, Req, Res } from '@nestjs/common';
import type { Request, Response } from 'express';
import { CurrentUserId } from '../common/current-user.decorator';
import { Public } from '../common/public.decorator';
import { DemoLoginDto } from './auth.dto';
import { AuthService } from './auth.service';

@Controller('api')
export class AuthController {
  constructor(private readonly auth: AuthService) {}

  @Public()
  @Get('auth/demo-accounts')
  accounts() { return this.auth.accounts(); }

  @Public()
  @Post('auth/demo-login')
  @HttpCode(200)
  async login(@Body() dto: DemoLoginDto, @Req() req: Request) {
    const user = await this.auth.login(dto.account_key, String(req.id));
    // Rotate the session ID on every login to prevent session fixation.
    await new Promise<void>((resolve, reject) => req.session.regenerate((e) => e ? reject(e) : resolve()));
    req.session.actorId = user.id;
    await new Promise<void>((resolve, reject) => req.session.save((e) => e ? reject(e) : resolve()));
    return user;
  }

  @Public()
  @Post('auth/logout')
  @HttpCode(204)
  async logout(@Req() req: Request, @Res({ passthrough: true }) res: Response) {
    await new Promise<void>((resolve, reject) => req.session.destroy((e) => e ? reject(e) : resolve()));
    res.clearCookie('enterprise.sid', { path: '/', httpOnly: true, sameSite: 'strict' });
  }

  @Get('me')
  me(@CurrentUserId() userId: string, @Req() req: Request) {
    return this.auth.me(userId, String(req.id));
  }
}

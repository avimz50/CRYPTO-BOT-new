import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
// Restrict CORS to Replit domains and localhost only.
// Wildcard '*' would allow any third-party site to read trading telemetry
// from a logged-in user's browser via cross-origin fetch.
const _CORS_ALLOW = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$|^https:\/\/[a-z0-9-]+\.(replit\.app|replit\.dev|repl\.co)(\/.*)?$/i;
app.use(cors({
  origin: (origin, cb) => {
    // Allow server-to-server (no Origin header) and matching browser origins
    if (!origin || _CORS_ALLOW.test(origin)) return cb(null, true);
    cb(new Error("CORS: origin not allowed"));
  },
  optionsSuccessStatus: 200,
}));
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use("/api", router);

export default app;

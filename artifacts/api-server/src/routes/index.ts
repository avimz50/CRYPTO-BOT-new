import { Router, type IRouter } from "express";
import healthRouter from "./health";
import botRouter from "./bot";
import pushRouter from "./push";

const router: IRouter = Router();

router.use(healthRouter);
router.use(botRouter);
router.use("/push", pushRouter);

export default router;

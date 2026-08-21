import { DashboardQueue } from "../lib/dashboard-queue.mjs";
import { dispatchQueueDrain } from "../lib/github-dispatch.mjs";
import { verifyGitHubOidcRequest } from "../lib/github-oidc.mjs";

const MAX_REQUEST_BYTES = 64 * 1024;
const ACKNOWLEDGMENT_OUTCOMES = new Set(["success", "retry", "dead"]);

export default async (request) => {
  try {
    return await handleQueueWorkerRequest(request);
  } catch (error) {
    console.error(error);
    return Response.json(
      { error: error.publicMessage || "internal server error" },
      { status: error.statusCode || 500 },
    );
  }
};

export async function handleQueueWorkerRequest(
  request,
  {
    queue = new DashboardQueue(),
    verifyRequest = verifyGitHubOidcRequest,
    dispatchDrain = dispatchQueueDrain,
  } = {},
) {
  if (request.method !== "POST") {
    return Response.json({ error: "method not allowed" }, { status: 405 });
  }
  await verifyRequest(request);
  const body = await readJsonBody(request);
  const action = body.action;
  switch (action) {
    case "activate":
      return jsonResponse({
        activated: await queue.activateDispatcher({
          generation: positiveInteger(body.generation, "generation"),
          workerId: workerId(body.workerId),
        }),
      });
    case "claim":
      return jsonResponse({
        claims: await queue.claimWave({
          generation: positiveInteger(body.generation, "generation"),
          workerId: workerId(body.workerId),
          limit: optionalPositiveInteger(body.limit, "limit") || 4,
        }),
      });
    case "heartbeat":
      return jsonResponse(await queue.heartbeat({
        generation: positiveInteger(body.generation, "generation"),
        workerId: workerId(body.workerId),
      }));
    case "acknowledge":
      return jsonResponse(await queue.acknowledge({
        itemKey: nonEmptyString(body.itemKey, "itemKey", 500),
        claimGeneration: positiveInteger(
          body.claimGeneration,
          "claimGeneration",
        ),
        workerId: workerId(body.workerId),
        outcome: acknowledgmentOutcome(body.outcome),
        error: typeof body.error === "string" ? body.error : "",
        retryAfterMs: optionalNonNegativeInteger(
          body.retryAfterMs,
          "retryAfterMs",
        ) || 0,
        operationId: body.operationId === undefined
          ? ""
          : nonEmptyString(body.operationId, "operationId", 200),
      }));
    case "finish": {
      const finish = {
        generation: positiveInteger(body.generation, "generation"),
        workerId: workerId(body.workerId),
      };
      const result = await queue.finishDispatcher(finish);
      const dispatch = result.requested
        ? await queue.claimFinishDispatch(finish)
        : "completed";
      if (dispatch === "in_progress" || dispatch === "unavailable") {
        return Response.json(
          { error: "successor dispatch is not yet confirmed" },
          { status: 503 },
        );
      }
      if (dispatch === "claimed") {
        try {
          await dispatchDrain(result.generation);
        } catch (error) {
          if (error.statusCode) {
            await queue.failFinishDispatchWithRelease(finish, {
              generation: result.generation,
              requestOwner: result.requestOwner,
            });
          }
          throw error;
        }
        await queue.completeFinishDispatch(finish);
      }
      return jsonResponse(result);
    }
    case "stats":
      return jsonResponse(await queue.stats());
    default:
      return Response.json({ error: "unsupported action" }, { status: 400 });
  }
}

async function readJsonBody(request) {
  const text = await request.text();
  if (Buffer.byteLength(text, "utf8") > MAX_REQUEST_BYTES) {
    throw requestError(413, "request body is too large");
  }
  try {
    const body = JSON.parse(text);
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      throw requestError(400, "request body must be a JSON object");
    }
    return body;
  } catch (error) {
    if (error.statusCode) {
      throw error;
    }
    throw requestError(400, `invalid JSON body: ${error.message}`);
  }
}

function workerId(value) {
  return nonEmptyString(value, "workerId", 200);
}

function acknowledgmentOutcome(value) {
  if (!ACKNOWLEDGMENT_OUTCOMES.has(value)) {
    throw requestError(400, "outcome must be one of: success, retry, dead");
  }
  return value;
}

function positiveInteger(value, name) {
  if (!Number.isInteger(value) || value < 1) {
    throw requestError(400, `${name} must be a positive integer`);
  }
  return value;
}

function optionalPositiveInteger(value, name) {
  if (value === undefined || value === null) {
    return null;
  }
  return positiveInteger(value, name);
}

function optionalNonNegativeInteger(value, name) {
  if (value === undefined || value === null) {
    return null;
  }
  if (!Number.isInteger(value) || value < 0) {
    throw requestError(400, `${name} must be a non-negative integer`);
  }
  return value;
}

function nonEmptyString(value, name, maxLength) {
  if (
    typeof value !== "string" ||
    value.length < 1 ||
    value.length > maxLength
  ) {
    throw requestError(
      400,
      `${name} must be a non-empty string no longer than ${maxLength} characters`,
    );
  }
  return value;
}

function jsonResponse(body) {
  return Response.json(body, { status: 200 });
}

function requestError(statusCode, message) {
  const error = new Error(message);
  error.statusCode = statusCode;
  error.publicMessage = message;
  return error;
}

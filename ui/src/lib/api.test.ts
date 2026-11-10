import { afterEach, describe, expect, it, vi } from "vitest"
import {
  ApiError,
  SessionRequiredError,
  createProject,
  createRule,
  deleteSource,
  getSecurityStatus,
  logout,
} from "./api"

function stubFetch(...responses: Response[]) {
  const fetchMock = vi.fn<typeof fetch>()
  responses.forEach((response) => fetchMock.mockResolvedValueOnce(response))
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

describe("api", () => {
  it("convierte un 401 en SessionRequiredError", async () => {
    stubFetch(new Response(null, { status: 401 }))
    await expect(getSecurityStatus()).rejects.toBeInstanceOf(SessionRequiredError)
  })

  it("envia el token CSRF en las operaciones que cambian datos", async () => {
    const fetchMock = stubFetch(
      Response.json({ csrf_token: "token-de-prueba" }),
      new Response(null, { status: 204 }),
    )
    await logout()
    const [path, init] = fetchMock.mock.calls[1]
    expect(path).toBe("/api/v1/session/logout")
    expect(init?.method).toBe("POST")
    expect(init?.headers).toEqual({ "X-CSRF-Token": "token-de-prueba" })
  })

  it("envia los proyectos nuevos como JSON con CSRF", async () => {
    const fetchMock = stubFetch(
      Response.json({ csrf_token: "token-de-prueba" }),
      Response.json({ id: "1", name: "Pagos" }, { status: 201 }),
    )
    await createProject({ name: "Pagos" })
    const [path, init] = fetchMock.mock.calls[1]
    expect(path).toBe("/api/v1/projects")
    expect(init?.headers).toEqual({
      "X-CSRF-Token": "token-de-prueba",
      "Content-Type": "application/json",
    })
    expect(init?.body).toBe(JSON.stringify({ name: "Pagos" }))
  })

  it("expone el codigo de error y los campos rechazados", async () => {
    stubFetch(
      Response.json({ csrf_token: "t" }),
      Response.json(
        { error: "invalid_request", fields: [{ loc: ["body", "project_key"], type: "x" }] },
        { status: 422 },
      ),
    )
    const error = await createProject({ name: "Pagos" }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe("invalid_request")
    expect((error as ApiError).fields).toEqual(["project_key"])
  })

  it("borra fuentes con DELETE y CSRF", async () => {
    const fetchMock = stubFetch(
      Response.json({ csrf_token: "t" }),
      new Response(null, { status: 204 }),
    )
    await deleteSource("abc", true)
    const [path, init] = fetchMock.mock.calls[1]
    expect(path).toBe("/api/v1/sources/abc?delete_citing=true")
    expect(init?.method).toBe("DELETE")
    expect(init?.headers).toEqual({ "X-CSRF-Token": "t" })
  })

  it("crea reglas a mano con POST, JSON y CSRF", async () => {
    const fetchMock = stubFetch(
      Response.json({ csrf_token: "t" }),
      Response.json({ entity_id: "r1" }, { status: 201 }),
    )
    const rule = {
      title: "Comisión",
      statement: "Un 2 % en pagos internacionales.",
      category: "calculation" as const,
      criticality: "high" as const,
    }
    await createRule("p1", rule)
    const [path, init] = fetchMock.mock.calls[1]
    expect(path).toBe("/api/v1/projects/p1/rules")
    expect(init?.method).toBe("POST")
    expect(init?.body).toBe(JSON.stringify(rule))
  })

  it("solo llama a rutas del mismo origen", async () => {
    const fetchMock = stubFetch(Response.json({}))
    await getSecurityStatus()
    expect(String(fetchMock.mock.calls[0][0]).startsWith("/")).toBe(true)
    expect(fetchMock.mock.calls[0][1]?.credentials).toBe("same-origin")
  })
})

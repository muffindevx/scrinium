import { Link } from "react-router"
import { t } from "@/lib/i18n"

function Command({ children }: { children: string }) {
  return <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs">{children}</pre>
}

/** Rules are written by a model the person provides: how to install one and choose it. */
export function ModelNeeded() {
  return (
    <div className="grid gap-3 rounded-lg border border-border p-4 text-sm">
      <h3 className="font-semibold">{t("Para extraer reglas necesitas un modelo")}</h3>
      <p className="text-muted-foreground">
        {t("Scrinium no trae el modelo que escribe las reglas: lo pones tú. Sin él no se extrae ninguna regla, tenga o no Git el proyecto. Elige uno de estos dos caminos.")}
      </p>
      <div className="grid gap-2">
        <h4 className="font-medium">{t("1. Un modelo local, en esta máquina")}</h4>
        <p className="text-muted-foreground">
          {t("Instala un servidor de modelos (Ollama, llama.cpp o vLLM) y descarga un modelo para código. Con Ollama, desde ollama.com:")}
        </p>
        <Command>{"ollama pull <modelo-de-código>"}</Command>
        <p className="text-muted-foreground">
          {t("Un modelo pequeño (3–8B) se deja reglas. Para proyectos medianos usa uno de código de 24–32B; el README indica el equipo que necesita cada tamaño.")}
        </p>
        <p className="text-muted-foreground">
          {t("Si usas Docker, el modelo corre fuera del contenedor. En Linux, haz que el servidor escuche en la dirección de Docker y arranca Scrinium con su puerto:")}
        </p>
        <Command>{"OLLAMA_HOST=172.17.0.1:11434 ollama serve\nsh docker/scrinium.sh start --model-port 11434"}</Command>
        <p className="text-muted-foreground">
          {t("Después elige ese modelo en")}{" "}
          <Link className="font-medium text-foreground underline underline-offset-4" to="/ajustes/modelos">
            {t("Ajustes › Modelos")}
          </Link>
          .
        </p>
      </div>
      <div className="grid gap-2">
        <h4 className="font-medium">{t("2. Tu propio proveedor")}</h4>
        <p className="text-muted-foreground">
          {t("DeepSeek, GLM, Claude o cualquier API compatible con OpenAI. Añádelo con su clave en Ajustes › Modelos y permite al proyecto usarlo para escribir reglas. El código sale de tu máquina hacia ese proveedor. Con Docker, antes abre el llavero: sh docker/scrinium.sh unlock.")}
        </p>
      </div>
    </div>
  )
}

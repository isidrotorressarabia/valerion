"""Páginas legales (aviso de privacidad, términos y envíos/devoluciones).

Los datos del responsable se leen del entorno. Mientras falten se muestran marcados como
«[COMPLETAR]» para que ningún texto se publique con datos inventados.
Los textos son una base de trabajo y deben ser revisados por un abogado antes de operar.
"""

from fastapi import Request

LEGAL_VERSION = "2026-11"
LEGAL_DATE = "13 de noviembre de 2026"
FIELDS = {
    "name": ("LEGAL_NAME", "razón social o nombre del responsable"),
    "address": ("LEGAL_ADDRESS", "domicilio del responsable"),
    "email": ("LEGAL_EMAIL", "correo para solicitudes de privacidad"),
    "rfc": ("LEGAL_RFC", "RFC"),
    "coverage": ("LEGAL_COVERAGE", "zonas de entrega"),
    "shipping": ("LEGAL_SHIPPING", "costo de envío"),
    "lead_time": ("LEGAL_LEAD_TIME", "tiempos de preparación y entrega"),
    "return_window": ("LEGAL_RETURN_WINDOW", "plazo para reportar daños o errores"),
    "jurisdiction": ("LEGAL_JURISDICTION", "ciudad y estado de los tribunales competentes"),
    "prices": ("LEGAL_PRICES_NOTE", "si los precios incluyen impuestos"),
}
PAGES = {
    "/privacidad": ("privacidad", "Aviso de Privacidad"),
    "/terminos": ("terminos", "Términos y Condiciones"),
    "/envios-devoluciones": ("envios", "Envíos, cancelaciones y devoluciones"),
}


def legal_values(config) -> dict:
    return {key: (config.get(env, "") or "").strip() for key, (env, _) in FIELDS.items()}


def missing_fields(config) -> list[str]:
    return [env for key, (env, _) in FIELDS.items() if not legal_values(config)[key]]


def attach(app, templates, page_context, config) -> None:
    values = legal_values(config)
    pending = [label for key, (_, label) in FIELDS.items() if not values[key]]

    def make(slug: str, title: str):
        def view(request: Request):
            return templates.TemplateResponse(
                request=request,
                name="legal.html",
                context={
                    **page_context(),
                    "page": slug,
                    "title": title,
                    "L": values,
                    "pending": pending,
                    "version": LEGAL_VERSION,
                    "updated": LEGAL_DATE,
                },
            )

        return view

    for path, (slug, title) in PAGES.items():
        app.get(path, include_in_schema=False)(make(slug, title))

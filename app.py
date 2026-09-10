import streamlit as st
import pandas as pd
from datetime import date
from dateutil.relativedelta import relativedelta
from supabase import create_client, Client

st.set_page_config(page_title="Cobranza Inmobiliaria", layout="wide")

# =========================================================
# Conexión a Supabase
# =========================================================
@st.cache_resource
def get_client() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = get_client()

# =========================================================
# Reglas del plan de pagos: 17 cuotas
# 0 = inicial (5000) | 1..15 = mensuales (1000) | 16 = final (10000)
# =========================================================
TOTAL_CUOTAS = 17
TOTAL_CASA = 30000

def cuota_monto(i):
    if i == 0:
        return 5000
    if i == 16:
        return 10000
    return 1000

# =========================================================
# Datos
# =========================================================
def cargar_datos():
    prospectos = supabase.table("prospectos").select("*").order("id").execute().data
    pagos = supabase.table("pagos").select("*").order("fecha").execute().data
    return prospectos, pagos

def pagos_de(prospecto_id, pagos):
    return sorted([p for p in pagos if p["prospecto_id"] == prospecto_id], key=lambda p: p["cuota_idx"])

def calcular(prospecto, pagos_cliente):
    inicio = date.fromisoformat(prospecto["inicio"])
    pagadas = len(pagos_cliente)
    completado = pagadas >= TOTAL_CUOTAS
    pagado = sum(cuota_monto(i) for i in range(pagadas))
    saldo = TOTAL_CASA - pagado

    if completado:
        proxima_idx = None
        fecha_proxima = None
        dias_hasta = 9999
        monto_proxima = 0
        chip = "done"
    else:
        proxima_idx = pagadas
        fecha_proxima = inicio + relativedelta(months=proxima_idx)
        dias_hasta = (fecha_proxima - date.today()).days
        monto_proxima = cuota_monto(proxima_idx)
        if dias_hasta < 0:
            chip = "late"
        elif dias_hasta <= 1:
            chip = "soon"
        else:
            chip = "ok"

    return {
        "pagadas": pagadas, "completado": completado, "pagado": pagado, "saldo": saldo,
        "proxima_idx": proxima_idx, "fecha_proxima": fecha_proxima,
        "dias_hasta": dias_hasta, "monto_proxima": monto_proxima, "chip": chip,
    }

CHIP_LABEL = {"ok": "Al día", "soon": "Vence mañana", "late": "Atrasado", "done": "Completado"}
CHIP_COLOR = {"ok": "#1F5C3F", "soon": "#B9722A", "late": "#A83A2E", "done": "#9C7A22"}
CHIP_BG = {"ok": "#DCE9DF", "soon": "#F3E3CE", "late": "#F2DAD4", "done": "#EFE3BC"}

def soles(n):
    return f"S/ {n:,.0f}"

# =========================================================
# Interfaz
# =========================================================
st.title("Registro de Cobranza — Inmobiliaria")
st.caption("Quién te debe, cuánto y cuándo le toca pagar, en un vistazo.")

prospectos, pagos = cargar_datos()

filas = []
for p in prospectos:
    pc = pagos_de(p["id"], pagos)
    d = calcular(p, pc)
    filas.append({**p, **d})

# ---------- Estadísticas ----------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Clientes activos", len(filas))
c2.metric("Te deben hoy", sum(1 for f in filas if f["chip"] == "late"))
c3.metric("Vencen mañana", sum(1 for f in filas if f["chip"] == "soon"))
total_posible = len(filas) * TOTAL_CASA
total_cobrado = sum(f["pagado"] for f in filas)
pct = round(total_cobrado / total_posible * 100) if total_posible else 0
c4.metric("Cobrado del total", f"{pct}%")

st.divider()

# ---------- Agregar prospecto ----------
with st.expander("➕ Agregar prospecto nuevo"):
    with st.form("nuevo_prospecto", clear_on_submit=True):
        colA, colB = st.columns(2)
        nombre = colA.text_input("Nombre completo")
        dni = colB.text_input("DNI")
        telefono = colA.text_input("Teléfono")
        inicio = colB.date_input("Fecha de inicio del plan", value=date.today())
        enviado = st.form_submit_button("Guardar prospecto")
        if enviado:
            if not nombre.strip():
                st.error("Ingresa al menos el nombre.")
            else:
                nuevo = supabase.table("prospectos").insert({
                    "nombre": nombre.strip(),
                    "dni": dni.strip() or None,
                    "telefono": telefono.strip() or None,
                    "inicio": inicio.isoformat(),
                }).execute().data[0]
                # la cuota inicial (índice 0) se registra pagada el día de la firma
                supabase.table("pagos").insert({
                    "prospecto_id": nuevo["id"], "cuota_idx": 0, "fecha": inicio.isoformat(),
                }).execute()
                st.success(f"{nombre} agregado correctamente.")
                st.rerun()

# ---------- Lista + ranking ----------
st.subheader("Prospectos")

if not filas:
    st.info("Todavía no hay prospectos registrados. Usa '➕ Agregar prospecto nuevo' arriba para empezar.")
else:
    orden = st.radio("Ordenar por:", ["Más cerca de terminar", "Más lejos de terminar", "Más urgente"], horizontal=True)

    if orden == "Más cerca de terminar":
        filas.sort(key=lambda f: (-f["pagadas"], f["saldo"]))
    elif orden == "Más lejos de terminar":
        filas.sort(key=lambda f: (f["pagadas"], -f["saldo"]))
    else:
        filas.sort(key=lambda f: f["dias_hasta"])

    tabla = pd.DataFrame([{
        "Nombre": f["nombre"],
        "DNI": f.get("dni") or "—",
        "Cuotas": f"{f['pagadas']}/{TOTAL_CUOTAS}",
        "Saldo": soles(f["saldo"]),
        "Estado": CHIP_LABEL[f["chip"]] + (f" ({abs(f['dias_hasta'])}d)" if f["chip"] == "late" else ""),
    } for f in filas])

    def color_estado(val):
        for chip, label in CHIP_LABEL.items():
            if val.startswith(label):
                return f"background-color:{CHIP_BG[chip]}; color:{CHIP_COLOR[chip]}; font-weight:600;"
        return ""

    st.dataframe(
        tabla.style.map(color_estado, subset=["Estado"]),
        use_container_width=True, hide_index=True,
    )

# ---------- Detalle de un cliente ----------
st.subheader("Ficha del cliente")
nombres = {f["id"]: f["nombre"] for f in filas}
if not filas:
    st.info("Agrega tu primer prospecto arriba para ver su ficha aquí.")
else:
    seleccion_id = st.selectbox(
        "Selecciona un cliente", options=list(nombres.keys()),
        format_func=lambda i: nombres[i],
    )
    f = next(x for x in filas if x["id"] == seleccion_id)

    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown(f"### {f['nombre']}")
        st.caption(f"DNI {f.get('dni') or '—'} · Tel. {f.get('telefono') or '—'}")
        st.progress(f["pagadas"] / TOTAL_CUOTAS, text=f"{f['pagadas']} de {TOTAL_CUOTAS} cuotas pagadas")

        kcol1, kcol2, kcol3 = st.columns(3)
        kcol1.metric("Pagado", soles(f["pagado"]))
        kcol2.metric("Saldo", soles(f["saldo"]))
        kcol3.metric("Inicio", date.fromisoformat(f["inicio"]).strftime("%d/%m/%Y"))

        with st.expander("Editar datos del cliente"):
            with st.form(f"editar_{f['id']}"):
                n_nombre = st.text_input("Nombre", value=f["nombre"])
                n_dni = st.text_input("DNI", value=f.get("dni") or "")
                n_tel = st.text_input("Teléfono", value=f.get("telefono") or "")
                if st.form_submit_button("Guardar cambios"):
                    supabase.table("prospectos").update({
                        "nombre": n_nombre.strip(), "dni": n_dni.strip() or None,
                        "telefono": n_tel.strip() or None,
                    }).eq("id", f["id"]).execute()
                    st.success("Datos actualizados.")
                    st.rerun()

        if st.button("🗑️ Eliminar prospecto", key=f"del_{f['id']}"):
            supabase.table("prospectos").delete().eq("id", f["id"]).execute()
            st.success("Prospecto eliminado.")
            st.rerun()

    with col2:
        if f["completado"]:
            st.success("🎉 Plan completado — casa pagada al 100%.")
        else:
            st.markdown(f"**Registrar cobro**")
            st.caption(
                f"Cuota {f['proxima_idx']+1} de {TOTAL_CUOTAS} · {soles(f['monto_proxima'])} "
                f"· vence {f['fecha_proxima'].strftime('%d/%m/%Y')}"
            )
            fecha_pago = st.date_input("Fecha del pago", value=date.today(), key=f"fecha_{f['id']}")
            if st.button("✅ Marcar como pagado", key=f"pagar_{f['id']}"):
                supabase.table("pagos").insert({
                    "prospecto_id": f["id"], "cuota_idx": f["proxima_idx"], "fecha": fecha_pago.isoformat(),
                }).execute()
                st.success("Pago registrado.")
                st.rerun()

            if f["pagadas"] > 0:
                if st.button("Deshacer el último pago", key=f"undo_{f['id']}"):
                    pc = pagos_de(f["id"], pagos)
                    ultimo = pc[-1]
                    supabase.table("pagos").delete().eq("id", ultimo["id"]).execute()
                    st.success("Último pago eliminado.")
                    st.rerun()

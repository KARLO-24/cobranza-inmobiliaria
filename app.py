import streamlit as st
import pandas as pd
from datetime import date
from dateutil.relativedelta import relativedelta
from supabase import create_client, Client

st.set_page_config(page_title="Cobranza Inmobiliaria", layout="wide")

@st.cache_resource
def get_client() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = get_client()

def soles(n):
    n = float(n or 0)
    return f"S/ {n:,.2f}" if n % 1 else f"S/ {n:,.0f}"

# =========================================================
# Datos
# =========================================================
def cargar_datos():
    prospectos = supabase.table("prospectos").select("*").order("id").execute().data
    cuotas = supabase.table("cuotas").select("*").order("numero").execute().data
    return prospectos, cuotas

def cuotas_de(pid, cuotas):
    return sorted([c for c in cuotas if c["prospecto_id"] == pid], key=lambda c: c["numero"])

def calcular(p, cuotas_cli):
    monto_total = sum(float(c["monto"]) for c in cuotas_cli)
    pagado = sum(float(c["importe_pagado"] or 0) for c in cuotas_cli if c["pagado"])
    saldo = round(monto_total - pagado, 2)
    pendientes = [c for c in cuotas_cli if not c["pagado"]]
    completado = len(pendientes) == 0

    if completado:
        chip, dias_hasta, proxima = "done", 9999, None
    else:
        proxima = min(pendientes, key=lambda c: c["fecha_vencimiento"])
        fecha_prox = date.fromisoformat(proxima["fecha_vencimiento"])
        dias_hasta = (fecha_prox - date.today()).days
        if dias_hasta < 0:
            chip = "late"
        elif dias_hasta <= 1:
            chip = "soon"
        else:
            chip = "ok"

    pct = round((pagado / monto_total) * 100) if monto_total else 0
    return {
        "monto_total": monto_total, "pagado": pagado, "saldo": saldo,
        "completado": completado, "proxima": proxima, "dias_hasta": dias_hasta,
        "chip": chip, "pct": pct,
    }

CHIP_LABEL = {"ok": "Al día", "soon": "Vence mañana", "late": "Atrasado", "done": "Completado"}
CHIP_COLOR = {"ok": "#1F5C3F", "soon": "#B9722A", "late": "#A83A2E", "done": "#9C7A22"}
CHIP_BG = {"ok": "#DCE9DF", "soon": "#F3E3CE", "late": "#F2DAD4", "done": "#EFE3BC"}

# =========================================================
st.title("Registro de Cobranza")
st.caption("Quién te debe, cuánto y cuándo le toca pagar, en un vistazo.")

prospectos, cuotas = cargar_datos()
filas = []
for p in prospectos:
    cc = cuotas_de(p["id"], cuotas)
    d = calcular(p, cc)
    filas.append({**p, **d, "cuotas": cc})

# ---------- Estadísticas ----------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Clientes activos", len(filas))
c2.metric("Te deben hoy", sum(1 for f in filas if f["chip"] == "late"))
c3.metric("Vencen mañana", sum(1 for f in filas if f["chip"] == "soon"))
total_posible = sum(f["monto_total"] for f in filas)
total_cobrado = sum(f["pagado"] for f in filas)
c4.metric("Cobrado del total", f"{round(total_cobrado/total_posible*100) if total_posible else 0}%")

st.divider()

# ---------- Registrar cliente nuevo ----------
with st.expander("➕ Registrar cliente nuevo"):
    colA, colB = st.columns(2)
    nombre = colA.text_input("Nombre completo", key="reg_nombre")
    dni = colB.text_input("DNI", key="reg_dni")
    telefono = colA.text_input("Celular", key="reg_tel")
    forma_pago = colB.radio("Forma de pago", ["CREDITO", "CONTADO"], horizontal=True, key="reg_forma")
    fecha_inicio = st.date_input("Fecha de inicio", value=date.today(), key="reg_inicio")

    if forma_pago == "CONTADO":
        monto_contado = st.number_input("Monto pagado al contado (S/)", min_value=0.0, step=100.0, key="reg_contado")
        if st.button("Registrar cliente al contado"):
            if not nombre.strip() or monto_contado <= 0:
                st.error("Ingresa el nombre y el monto.")
            else:
                nuevo = supabase.table("prospectos").insert({
                    "nombre": nombre.strip(), "dni": dni.strip() or None, "telefono": telefono.strip() or None,
                    "forma_pago": "CONTADO", "fecha_inicio": fecha_inicio.isoformat(),
                }).execute().data[0]
                supabase.table("cuotas").insert({
                    "prospecto_id": nuevo["id"], "numero": 0, "fecha_vencimiento": fecha_inicio.isoformat(),
                    "monto": monto_contado, "pagado": True,
                    "fecha_pago": fecha_inicio.isoformat(), "importe_pagado": monto_contado,
                }).execute()
                st.success(f"{nombre} registrado (pago al contado).")
                st.rerun()
    else:
        st.markdown("**Cronograma de pagos al crédito**")
        c1_, c2_, c3_ = st.columns(3)
        monto_inicial = c1_.number_input("Cuota inicial (S/)", min_value=0.0, step=100.0, key="reg_inicial")
        n_cuotas = c2_.number_input("N° de cuotas mensuales", min_value=0, step=1, key="reg_ncuotas")
        monto_cuota = c3_.number_input("Monto de cada cuota mensual (S/)", min_value=0.0, step=50.0, key="reg_montocuota")

        total_preview = monto_inicial + n_cuotas * monto_cuota
        st.caption(f"Total del crédito: {soles(total_preview)} ({int(n_cuotas)+1 if monto_inicial>0 else int(n_cuotas)} cuotas en total)")

        if st.button("Generar cronograma y registrar cliente"):
            if not nombre.strip():
                st.error("Ingresa el nombre.")
            elif monto_inicial <= 0 and n_cuotas == 0:
                st.error("Ingresa al menos la cuota inicial o las cuotas mensuales.")
            else:
                nuevo = supabase.table("prospectos").insert({
                    "nombre": nombre.strip(), "dni": dni.strip() or None, "telefono": telefono.strip() or None,
                    "forma_pago": "CREDITO", "fecha_inicio": fecha_inicio.isoformat(),
                }).execute().data[0]
                filas_cuotas = []
                numero = 0
                if monto_inicial > 0:
                    filas_cuotas.append({
                        "prospecto_id": nuevo["id"], "numero": 0,
                        "fecha_vencimiento": fecha_inicio.isoformat(), "monto": monto_inicial,
                    })
                    numero = 1
                for i in range(int(n_cuotas)):
                    fecha_c = fecha_inicio + relativedelta(months=numero)
                    filas_cuotas.append({
                        "prospecto_id": nuevo["id"], "numero": numero,
                        "fecha_vencimiento": fecha_c.isoformat(), "monto": monto_cuota,
                    })
                    numero += 1
                supabase.table("cuotas").insert(filas_cuotas).execute()
                st.success(f"{nombre} registrado con su cronograma de {len(filas_cuotas)} cuotas.")
                st.rerun()

# ---------- Lista + ranking ----------
st.subheader("Prospectos")

if not filas:
    st.info("Todavía no hay clientes registrados. Usa '➕ Registrar cliente nuevo' arriba para empezar.")
else:
    orden = st.radio("Ordenar por:", ["Más cerca de terminar", "Más lejos de terminar", "Más urgente"], horizontal=True)
    if orden == "Más cerca de terminar":
        filas.sort(key=lambda f: (-f["pct"], f["saldo"]))
    elif orden == "Más lejos de terminar":
        filas.sort(key=lambda f: (f["pct"], -f["saldo"]))
    else:
        filas.sort(key=lambda f: f["dias_hasta"])

    tabla = pd.DataFrame([{
        "Nombre": f["nombre"], "DNI": f.get("dni") or "—",
        "Forma": f.get("forma_pago") or "—",
        "Avance": f"{f['pct']}%", "Saldo": soles(f["saldo"]),
        "Estado": CHIP_LABEL[f["chip"]] + (f" ({abs(f['dias_hasta'])}d)" if f["chip"] == "late" else ""),
    } for f in filas])

    def color_estado(val):
        for chip, label in CHIP_LABEL.items():
            if val.startswith(label):
                return f"background-color:{CHIP_BG[chip]}; color:{CHIP_COLOR[chip]}; font-weight:600;"
        return ""

    st.dataframe(tabla.style.map(color_estado, subset=["Estado"]), use_container_width=True, hide_index=True)

# ---------- Ficha del cliente ----------
st.subheader("Ficha del cliente")
nombres = {f["id"]: f["nombre"] for f in filas}
if not filas:
    st.info("Registra tu primer cliente arriba para ver su ficha aquí.")
else:
    seleccion_id = st.selectbox("Selecciona un cliente", options=list(nombres.keys()), format_func=lambda i: nombres[i])
    f = next(x for x in filas if x["id"] == seleccion_id)

    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown(f"### {f['nombre']}")
        st.caption(f"DNI {f.get('dni') or '—'} · Cel. {f.get('telefono') or '—'} · {f.get('forma_pago')}")
        st.progress(min(f["pct"] / 100, 1.0), text=f"{f['pct']}% pagado")

        k1, k2, k3 = st.columns(3)
        k1.metric("Pagado", soles(f["pagado"]))
        k2.metric("Saldo (debe)", soles(f["saldo"]))
        k3.metric("Deuda total", soles(f["monto_total"]))

        st.markdown("**Cronograma de pagos**")
        st.caption("Edita montos, fechas o el estado de pago directamente en la tabla. Puedes agregar cuotas nuevas (por ejemplo si la casa subió de precio) o eliminar filas con el ícono de la papelera. La cuota N° 0 es la inicial.")

        crono_df = pd.DataFrame([{
            "id": c["id"],
            "N°": c["numero"],
            "Vence": pd.to_datetime(c["fecha_vencimiento"]).date(),
            "Monto": float(c["monto"]),
            "Pagado": bool(c["pagado"]),
            "Fecha de pago": pd.to_datetime(c["fecha_pago"]).date() if c["fecha_pago"] else None,
            "Importe pagado": float(c["importe_pagado"]) if c["importe_pagado"] is not None else None,
        } for c in f["cuotas"]])

        edited = st.data_editor(
            crono_df,
            key=f"crono_{f['id']}",
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            column_order=["N°", "Vence", "Monto", "Pagado", "Fecha de pago", "Importe pagado"],
            column_config={
                "N°": st.column_config.NumberColumn("N° (0 = inicial)", step=1),
                "Vence": st.column_config.DateColumn("Vence"),
                "Monto": st.column_config.NumberColumn("Monto (S/)", min_value=0.0, step=50.0),
                "Pagado": st.column_config.CheckboxColumn("Pagado"),
                "Fecha de pago": st.column_config.DateColumn("Fecha de pago"),
                "Importe pagado": st.column_config.NumberColumn("Importe pagado (S/)", min_value=0.0, step=50.0),
            },
        )

        if st.button("💾 Guardar cambios en el cronograma", key=f"save_crono_{f['id']}"):
            ids_originales = set(crono_df["id"].dropna().astype(int))
            ids_editados = set(edited["id"].dropna().astype(int)) if "id" in edited.columns else set()
            for cid in ids_originales - ids_editados:
                supabase.table("cuotas").delete().eq("id", int(cid)).execute()

            for _, row in edited.iterrows():
                payload = {
                    "numero": int(row["N°"]) if pd.notna(row["N°"]) else 0,
                    "fecha_vencimiento": row["Vence"].isoformat() if pd.notna(row["Vence"]) else date.today().isoformat(),
                    "monto": float(row["Monto"]) if pd.notna(row["Monto"]) else 0,
                    "pagado": bool(row["Pagado"]) if pd.notna(row["Pagado"]) else False,
                    "fecha_pago": row["Fecha de pago"].isoformat() if pd.notna(row["Fecha de pago"]) else None,
                    "importe_pagado": float(row["Importe pagado"]) if pd.notna(row["Importe pagado"]) else None,
                }
                tiene_id = "id" in row and pd.notna(row["id"])
                if tiene_id:
                    supabase.table("cuotas").update(payload).eq("id", int(row["id"])).execute()
                else:
                    payload["prospecto_id"] = f["id"]
                    supabase.table("cuotas").insert(payload).execute()

            st.success("Cronograma actualizado.")
            st.rerun()

        if st.button("🗑️ Eliminar cliente", key=f"del_{f['id']}"):
            supabase.table("prospectos").delete().eq("id", f["id"]).execute()
            st.success("Cliente eliminado.")
            st.rerun()

    with col2:
        if f["completado"]:
            st.success("🎉 Crédito completado — deuda pagada al 100%.")
        else:
            prox = f["proxima"]
            st.markdown("**Registrar pago**")
            st.caption(f"Cuota N° {prox['numero']} · {soles(prox['monto'])} · vence {prox['fecha_vencimiento']}")
            fecha_pago = st.date_input("Fecha del pago", value=date.today(), key=f"fecha_{f['id']}")
            importe = st.number_input("Importe pagado (S/)", min_value=0.0, step=50.0,
                                       value=float(prox["monto"]), key=f"importe_{f['id']}")
            if st.button("✅ Registrar pago", key=f"pagar_{f['id']}"):
                if importe <= 0:
                    st.error("Ingresa un importe mayor a 0.")
                else:
                    supabase.table("cuotas").update({
                        "pagado": True, "fecha_pago": fecha_pago.isoformat(), "importe_pagado": importe,
                    }).eq("id", prox["id"]).execute()
                    st.success("Pago registrado.")
                    st.rerun()

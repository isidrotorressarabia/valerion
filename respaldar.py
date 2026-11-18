"""Export application tables and optionally managed Cloudinary image bytes."""
import argparse
from pathlib import Path
from backups import create_backup,validate_backup
from maintenance import maintenance

def main():
    parser=argparse.ArgumentParser(description='Exporta un respaldo WEB 3.0 sin credenciales ni sesiones.')
    parser.add_argument('salida',type=Path);parser.add_argument('--imagenes',action='store_true')
    args=parser.parse_args()
    if args.salida.exists():parser.error('El archivo de salida ya existe. Usa otro nombre.')
    try:
        with maintenance() as app:manifest=create_backup(app,args.salida,args.imagenes)
        validate_backup(args.salida)
        print('Respaldo verificado:',args.salida,'·',manifest['counts'])
    except Exception as exc:
        args.salida.unlink(missing_ok=True)
        raise SystemExit('No se completó el respaldo ('+type(exc).__name__+'). Revisa conexión y permisos; no se guardó una copia parcial.') from None
if __name__=='__main__':main()

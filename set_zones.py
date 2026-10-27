"""
Remplace les zones par les 7 Directions de Zone de l'ANCAR et découple les
régions des zones (une région peut relever de plusieurs zones : plus de
rattachement rigide région -> zone).

Les régions, producteurs et parcelles ne sont pas touchés.
Usage : python set_zones.py
"""
from app import create_app
from app.extensions import db
from app.models.zone import Zone, Region
from app.models.user import User

app = create_app()

ZONES = [
    ("NIAYES", "Zone des Niayes"),
    ("BAN", "Bassin Arachidier Nord"),
    ("BAS", "Bassin Arachidier Sud"),
    ("BMC", "Basse et Moyenne Casamance"),
    ("SOHC", "Sénégal Oriental et Haute Casamance"),
    ("SP", "Zone Sylvo-Pastorale"),
    ("VFS", "Vallée du Fleuve Sénégal"),
]


def run():
    codes = {c for c, _ in ZONES}

    # 1. Découpler toutes les régions des zones
    Region.query.update({Region.zone_id: None})

    # 2. Supprimer les zones obsolètes (en détachant d'abord les utilisateurs)
    obsoletes = Zone.query.filter(~Zone.code.in_(codes)).all()
    ids = [z.id for z in obsoletes]
    if ids:
        User.query.filter(User.zone_id.in_(ids)).update(
            {User.zone_id: None}, synchronize_session=False
        )
        for z in obsoletes:
            db.session.delete(z)

    # 3. Créer / mettre à jour les 7 zones
    for code, nom in ZONES:
        z = Zone.query.filter_by(code=code).first()
        if z:
            z.nom = nom
        else:
            db.session.add(Zone(code=code, nom=nom))

    db.session.commit()


if __name__ == "__main__":
    with app.app_context():
        run()
        print("Zones ANCAR en place :")
        for z in Zone.query.order_by(Zone.nom).all():
            print(f"  {z.code:7s} {z.nom}")
        n_reg = Region.query.count()
        n_liees = Region.query.filter(Region.zone_id.isnot(None)).count()
        print(f"Régions : {n_reg} (dont {n_liees} encore liées à une zone) — découplage OK si 0.")

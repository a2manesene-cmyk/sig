# -*- coding: utf-8 -*-
from datetime import datetime
from app.extensions import db


class FinancementOP(db.Model):
    """Financement accordé à une OP (source : La Banque Agricole), par campagne."""
    __tablename__ = "financements_op"

    id = db.Column(db.Integer, primary_key=True)
    organisation_id = db.Column(db.Integer, db.ForeignKey("organisations_producteurs.id", ondelete="SET NULL"), index=True)
    programme_id = db.Column(db.Integer, db.ForeignKey("programmes.id"), index=True)
    raison_sociale = db.Column(db.String(150))   # nom de l'OP tel que dans le fichier
    region = db.Column(db.String(80))
    annee = db.Column(db.Integer)
    nb_producteurs = db.Column(db.Integer)
    superficie_demandee = db.Column(db.Float)
    montant_sollicite = db.Column(db.BigInteger)
    superficie_accordee = db.Column(db.Float)
    montant_accorde = db.Column(db.BigInteger)
    etat = db.Column(db.String(60))
    realisation = db.Column(db.BigInteger)
    nb_ha_facture = db.Column(db.Float)
    prime_ha = db.Column(db.Float)
    prime_total = db.Column(db.Float)
    source = db.Column(db.String(60), default="La Banque Agricole (LBA)")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    organisation = db.relationship("OrganisationProducteur")
    programme = db.relationship("Programme")

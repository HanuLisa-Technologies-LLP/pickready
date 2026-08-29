output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Where ECS tasks run. Egress through NAT, no inbound from the internet."
  value       = aws_subnet.private[*].id
}

output "data_subnet_ids" {
  description = "Where RDS and ElastiCache run. NO route to the internet in either direction."
  value       = aws_subnet.data[*].id
}

output "alb_security_group_id" {
  value = aws_security_group.alb.id
}

output "ecs_security_group_id" {
  value = aws_security_group.ecs.id
}

output "rds_security_group_id" {
  value = aws_security_group.rds.id
}

output "redis_security_group_id" {
  value = aws_security_group.redis.id
}
